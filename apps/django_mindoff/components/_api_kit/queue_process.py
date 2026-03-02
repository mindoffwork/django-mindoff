import json
import hashlib
import base64
import gzip
import redis
import dramatiq
import uuid
from django.utils import timezone
from django.urls import resolve
from django.test.client import RequestFactory
from ..helper_kit import get_api_class_from_url_name
from ...models import MOQueue
import traceback
from django.utils.dateparse import parse_datetime

from django.apps import apps
from django.utils.module_loading import import_string
from ...models import MOQueue
from .redis import (
    init_queue,
    mark_running,
    mark_completed,
    mark_failed,
    mark_cancelled,
    mark_cancel_requested,
    get_queue_status,
)
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import Retries
from django.conf import settings
from rest_framework.response import Response as DRFResponse
from django.http import JsonResponse
from ..validation_kit import MindoffValidationError
from ..response_kit import mo_response_kit

redis_broker = RedisBroker(
    url=settings.REDIS_URL,
    middleware=[
        Retries(max_retries=0),
    ],
)
dramatiq.set_broker(redis_broker)

_COMPRESSED_RESPONSE_FLAG = "__compressed__"


# ─────────────────────────────────────────────────────────────────────────────
# Lightweight request wrapper used inside the worker
# ─────────────────────────────────────────────────────────────────────────────


class QueueRequest:
    """
    A minimal, serialisation-safe stand-in for Django's ``HttpRequest``/DRF's
    ``Request`` used when executing API logic inside a Dramatiq worker.
    """

    def __init__(
        self,
        *,
        method: str,
        data=None,
        query_params=None,
        headers=None,
        user=None,
        auth=None,
        files=None,
    ):
        self.method = (method or "GET").upper()
        self.data = data or {}
        self.query_params = query_params or {}
        self.FILES = files or {}
        self.headers = headers or {}
        self.user = user
        self.auth = auth

    def __repr__(self):
        return f"<QueueRequest method={self.method}>"


def enqueue_process(*, request, api_instance, args, kwargs):
    """
    Persist a queue task (SQL + Redis) and dispatch it to a Dramatiq worker.

    Returns the ``queue_task_uuid`` string so the caller can hand it back to
    the HTTP client immediately.
    """
    # 1. Resolve owner identity (authenticated user or anonymous session)
    if request.user.is_authenticated:
        owner_id = str(request.user.id)
        user_obj = request.user
    else:
        if not request.session.session_key:
            request.session.create()
        owner_id = str(request.session.session_key)
        user_obj = None

    # 2. Build a serialisable snapshot of the request
    request_snapshot = {
        "method": request.method,
        "data": request.data if request.method in ("POST", "PUT") else {},
        "query_params": dict(getattr(request, "query_params", {})),
        "args": list(args),
        "kwargs": dict(kwargs),
    }

    # 3. Optional idempotency: deduplicate in-flight tasks with identical inputs
    idempotency_key = None
    if not getattr(api_instance, "allow_duplicate_queue", False):
        raw = json.dumps(
            {
                "owner_id": owner_id,
                "api_url_name": api_instance.api_url_name,
                "request": request_snapshot,
            },
            sort_keys=True,
            default=str,
        )
        idempotency_key = hashlib.sha256(raw.encode()).hexdigest()
        existing = (
            MOQueue.objects.filter(idempotency_key=idempotency_key)
            .exclude(status__in=("failed", "completed", "cancelled"))
            .first()
        )
        if existing:
            return str(existing.id)

    # 4. Persist queue task in the database
    queue_task_uuid = uuid.uuid4()
    enqueue_obj = MOQueue.objects.create(
        id=queue_task_uuid,
        owner_id=owner_id,
        user_ref=user_obj,
        idempotency_key=idempotency_key,
        api_url_name=api_instance.api_url_name,
        status="queued",
        request=request_snapshot,
    )

    # 5. Initialise Redis state (including progress_steps for the status endpoint)
    progress_steps = getattr(api_instance, "progress_steps", None) or {}
    init_queue(
        queue_task_uuid=str(queue_task_uuid),
        created_at=enqueue_obj.created_at,
        steps=progress_steps,
    )

    # 6. Dispatch to worker
    execute_queue.send(str(queue_task_uuid))
    return str(queue_task_uuid)


# ─────────────────────────────────────────────────────────────────────────────
# Worker actor
# ─────────────────────────────────────────────────────────────────────────────


@dramatiq.actor(max_retries=0)
def execute_queue(queue_task_uuid: str):
    """
    Entry point executed by a Dramatiq worker process.

    Lifecycle
    ---------
    queued → running → completed | failed | cancelled
    """
    # ── 1. Load the task record (SQL is the authoritative store) ──────────
    try:
        obj = MOQueue.objects.get(id=queue_task_uuid)
    except MOQueue.DoesNotExist:
        return

    snapshot = obj.request or {}

    # ── 2. Reconcile Redis state ──────────────────────────────────────────
    redis_state = _safe_get_queue_status(queue_task_uuid)
    if _state_status(redis_state) == "unknown":
        redis_state = rehydrate_queue_state(queue_task_uuid)

    # ── 3. Early exit if cancel was requested before we even started ──────
    #
    # We must check BOTH the Redis flag and the DB status because:
    #   a) Redis may have expired → rehydrate restores from DB.
    #   b) cancel_queue_task() only writes to Redis; _mark_db_cancelled()
    #      is called either here or at the next progress_checkpoint.
    if _is_cancel_requested(redis_state) or obj.status == "cancelled":
        _mark_db_cancelled(obj, queue_task_uuid)
        return

    # ── 4. Transition to "running" ────────────────────────────────────────
    obj.status = "running"
    obj.save(update_fields=["status"])
    mark_running(queue_task_uuid)

    try:
        # ── 5. Resolve and instantiate the API class ──────────────────────
        api_cls = get_api_class_from_url_name(api_url_name=obj.api_url_name)
        api = api_cls()

        # ── 6. Reconstruct the request object ────────────────────────────
        request = QueueRequest(
            method=snapshot.get("method"),
            data=snapshot.get("data"),
            query_params=snapshot.get("query_params"),
            headers=snapshot.get("headers"),
            user=obj.get_user(),
            auth=None,
            files={},
        )
        request.queue_task_uuid = str(obj.id)

        args = snapshot.get("args", [])
        kwargs = snapshot.get("kwargs", {})

        # ── 7. Execute the API logic ──────────────────────────────────────
        result = api.run(request, *args, **kwargs)

        # ── 8. Final cancellation check (no progress_checkpoint was called
        #       after the last one, or the developer never called one) ─────
        latest_redis_state = _safe_get_queue_status(queue_task_uuid)
        if _is_cancel_requested(latest_redis_state):
            _mark_db_cancelled(obj, queue_task_uuid)
            return

        # ── 9. Normalise, compress, and persist the result ────────────────
        result = _extract_result(result)
        result = _ensure_json_result(result)
        compressed_result = _compress_json_result(result)

        obj.status = "completed"
        obj.response = compressed_result
        obj.save(update_fields=["status", "response"])
        mark_completed(queue_task_uuid)

    except MindoffValidationError as exc:
        if exc.code == "QUEUE_TASK_CANCELLED":
            # progress_checkpoint raised this after confirming is_cancel=true
            _mark_db_cancelled(obj, queue_task_uuid)
            return

        tb = traceback.format_exc()
        obj.status = "failed"
        obj.error = {"message": str(exc), "traceback": tb, "code": exc.code}
        obj.save(update_fields=["status", "error"])
        mark_failed(queue_task_uuid, error=str(exc))

    except Exception as exc:
        tb = traceback.format_exc()
        obj.status = "failed"
        obj.error = {"message": str(exc), "traceback": tb}
        obj.save(update_fields=["status", "error"])
        mark_failed(queue_task_uuid, error=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# State management helpers
# ─────────────────────────────────────────────────────────────────────────────


def rehydrate_queue_state(queue_task_uuid: str) -> dict:
    """
    Ensure Redis reflects the ground-truth state stored in the database.

    Called whenever ``get_queue_status`` returns ``"unknown"`` (i.e. the Redis
    key has expired or was never written).

    Resolution order
    ----------------
    1. If neither DB nor Redis have a record → return ``unknown``.
    2. If only DB exists → sync Redis from DB.
    3. If both exist:
       a. Redis has a *newer* ``updated_at`` → trust Redis, sync DB from Redis.
       b. Otherwise → trust DB, sync Redis from DB.

    Cancel safety
    -------------
    ``mark_cancel_requested`` writes only to Redis and calls ``persist()`` so
    the key never expires while a cancel is pending.  If we reach this function
    it means the key *has* expired, which implies the worker finished before
    the cancel could be processed → the DB status is terminal and we simply
    restore Redis from DB.
    """
    redis_state = _safe_get_queue_status(queue_task_uuid)
    obj = MOQueue.objects.filter(id=queue_task_uuid).first()

    if obj is None and _state_status(redis_state) == "unknown":
        return {"job_status": "unknown", "is_cancel": False, "steps": {}}
    if obj is None:
        return redis_state

    redis_id = str(redis_state.get("id", "") or "")
    if redis_id and redis_id != str(obj.id):
        _sync_redis_from_db(obj)
        return _safe_get_queue_status(str(obj.id))

    if _state_status(redis_state) == "unknown":
        _sync_redis_from_db(obj)
        return _safe_get_queue_status(str(obj.id))

    redis_updated_at = _parse_updated_at(redis_state.get("updated_at"))
    db_updated_at = _parse_updated_at(obj.updated_at.isoformat())

    if redis_updated_at is not None and (
        db_updated_at is None or redis_updated_at > db_updated_at
    ):
        # Redis is fresher — propagate to DB then return fresh Redis state
        _sync_db_from_redis(obj, redis_state)
        obj.refresh_from_db()
        return _safe_get_queue_status(str(obj.id))

    # DB is fresher (or same) — restore Redis from DB
    _sync_redis_from_db(obj)
    return _safe_get_queue_status(str(obj.id))


def cancel_queue_task(queue_task_uuid: str) -> str:
    """
    Request cancellation of a queued or running task.

    Returns one of: ``"cancelled"`` | ``"not_found"`` | ``"not_cancellable"``

    Flow
    ----
    * We write ``is_cancel=true`` to Redis.  The worker checks this flag at
      every ``progress_checkpoint`` call and will honour it.
    * For tasks that are already in a terminal state the call is a no-op and
      ``"not_cancellable"`` is returned.
    """
    obj = MOQueue.objects.filter(id=queue_task_uuid).first()
    if not obj:
        return "not_found"

    redis_state = _safe_get_queue_status(queue_task_uuid)
    if _state_status(redis_state) == "unknown":
        redis_state = rehydrate_queue_state(queue_task_uuid)

    status = _state_status(redis_state)

    # Also cross-check DB to guard against stale Redis
    db_status = obj.status
    effective_status = status if status != "unknown" else db_status

    if effective_status in ("completed", "failed", "cancelled"):
        return "not_cancellable"

    if effective_status not in ("queued", "running"):
        return "not_cancellable"

    mark_cancel_requested(str(obj.id))
    return "cancelled"


def retry_failed_queue_task(queue_task_uuid: str) -> str:
    """
    Re-enqueue a previously failed task from scratch.

    Returns one of: ``"queued"`` | ``"not_found"`` | ``"not_retriable"``
    """
    obj = MOQueue.objects.filter(id=queue_task_uuid).first()
    if not obj:
        return "not_found"

    if obj.status != "failed":
        return "not_retriable"

    obj.status = "queued"
    obj.error = None
    obj.response = None
    obj.save(update_fields=["status", "error", "response", "updated_at"])

    # Re-resolve progress_steps so Redis has them again after the retry
    try:
        api_cls = get_api_class_from_url_name(api_url_name=obj.api_url_name)
        progress_steps = getattr(api_cls, "progress_steps", None) or {}
    except Exception:
        progress_steps = {}

    init_queue(
        queue_task_uuid=str(obj.id),
        created_at=timezone.now(),
        steps=progress_steps,
    )
    execute_queue.send(str(obj.id))
    return "queued"


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_json_result(result):
    try:
        json.dumps(result, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError("API result must be JSON-serializable") from exc
    return result


def _compress_json_result(result):
    payload = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
    compressed = gzip.compress(payload.encode("utf-8"))
    b64 = base64.b64encode(compressed).decode("ascii")
    return {
        _COMPRESSED_RESPONSE_FLAG: True,
        "codec": "gzip+base64",
        "data": b64,
    }


def _extract_result(result):
    """Normalise a DRF ``Response`` or Django ``JsonResponse`` to a plain dict."""
    if isinstance(result, DRFResponse):
        return {"status_code": result.status_code, "data": result.data}
    if isinstance(result, JsonResponse):
        return {"status_code": result.status_code, "data": json.loads(result.content)}
    return result


def _mark_db_cancelled(obj: MOQueue, queue_task_uuid: str):
    """
    Write the final ``"cancelled"`` state to both DB and Redis atomically-ish.

    We write DB first so that if Redis fails we still have a consistent record.
    """
    cancelled_result = _extract_result(
        mo_response_kit.json_response(
            code="QUEUE_TASK_CANCELLED",
            category="warning",
            data={"queue_id": str(queue_task_uuid), "status": "cancelled"},
        )
    )
    obj.status = "cancelled"
    obj.response = _compress_json_result(cancelled_result)
    obj.error = None
    obj.save(update_fields=["status", "response", "error"])
    mark_cancelled(str(queue_task_uuid))


def _safe_get_queue_status(queue_task_uuid: str) -> dict:
    try:
        return get_queue_status(queue_task_uuid)
    except Exception:
        return {"job_status": "unknown", "is_cancel": False, "steps": {}}


def _is_cancel_requested(redis_state: dict) -> bool:
    return _state_status(redis_state) == "cancelled" or bool(
        redis_state.get("is_cancel")
    )


def _state_status(redis_state: dict) -> str:
    return redis_state.get("job_status") or "unknown"


def _parse_updated_at(updated_at):
    if not updated_at:
        return None
    return parse_datetime(str(updated_at))


def _sync_redis_from_db(obj: MOQueue):
    """Rebuild Redis state from the DB record, preserving progress_steps."""
    # Re-resolve steps from the API class so they survive Redis expiry
    try:
        api_cls = get_api_class_from_url_name(api_url_name=obj.api_url_name)
        steps = getattr(api_cls, "progress_steps", None) or {}
    except Exception:
        steps = {}

    if obj.status == "queued":
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at, steps=steps)
    elif obj.status == "running":
        # init first so steps are present, then mark running
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at, steps=steps)
        mark_running(str(obj.id))
    elif obj.status == "completed":
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at, steps=steps)
        mark_completed(str(obj.id))
    elif obj.status == "failed":
        error_message = str((obj.error or {}).get("message", "failed"))
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at, steps=steps)
        mark_failed(str(obj.id), error=error_message)
    elif obj.status == "cancelled":
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at, steps=steps)
        mark_cancelled(str(obj.id))


def _sync_db_from_redis(obj: MOQueue, redis_state: dict):
    """Propagate a fresher Redis state back to the DB."""
    if _is_cancel_requested(redis_state):
        _mark_db_cancelled(obj, str(obj.id))
        return

    status = _state_status(redis_state)
    if status == "unknown":
        return
    if status == "cancelled":
        _mark_db_cancelled(obj, str(obj.id))
        return
    if obj.status == status:
        return

    obj.status = status
    if status in ("queued", "running", "completed"):
        obj.error = None
    if status == "failed":
        message = str(redis_state.get("progress", {}).get("current_message", "failed"))
        obj.error = {"message": message}
    obj.save(update_fields=["status", "error", "updated_at"])
