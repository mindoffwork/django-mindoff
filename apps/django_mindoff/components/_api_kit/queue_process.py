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
    redis_client,
)
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import Retries
from django.conf import settings
from rest_framework.response import Response as DRFResponse
from django.http import JsonResponse
from ..validation_kit import MindoffValidationError
from ..response_kit import mo_response_kit, MINDOFF_RESPONSES

redis_broker = RedisBroker(
    url=settings.REDIS_URL,
    middleware=[
        Retries(max_retries=0),
    ],
)
dramatiq.set_broker(redis_broker)

_COMPRESSED_RESPONSE_FLAG = "__compressed__"
_json_compression_string = "gzip+base64"

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
            .exclude(job_status__in=("failed", "completed", "cancelled"))
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
        job_status="queued",
        request=_compress_json_result_if_large(
            request_snapshot,
            _get_json_compression_min_bytes("request", default=4096),
        ),
    )

    # 5. Initialise Redis runtime state
    init_queue(
        queue_task_uuid=str(queue_task_uuid),
        created_at=enqueue_obj.created_at,
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

    snapshot = _decode_json_blob(obj.request) or {}
    if not isinstance(snapshot, dict):
        snapshot = {}

    # ── 2. Reconcile Redis state ──────────────────────────────────────────
    redis_state = _safe_get_queue_status(queue_task_uuid)
    if _state_status(redis_state) == "unknown":
        redis_state = rehydrate_queue_state(queue_task_uuid)

    # ── 3. Early exit if cancel was requested before we even started ──────
    if _is_cancel_requested(redis_state) or obj.job_status == "cancelled":
        _mark_db_cancelled(obj, queue_task_uuid)
        return

    # ── 4. Transition to "running" ────────────────────────────────────────
    obj.job_status = "running"
    obj.save(update_fields=["job_status"])
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

        # ── 8. Final cancellation check ───────────────────────────────────
        latest_redis_state = _safe_get_queue_status(queue_task_uuid)
        if _is_cancel_requested(latest_redis_state):
            _mark_db_cancelled(obj, queue_task_uuid)
            return

        # ── 9. Extract response_code before normalising the result ────────
        response_code = _extract_response_code(result)

        # ── 10. Normalise, compress, and persist the result ───────────────
        result = _extract_result(result)
        result = _ensure_json_result(result)
        compressed_result = _compress_json_result_if_large(
            result,
            _get_json_compression_min_bytes("response", default=0),
        )

        obj.job_status = "completed"
        obj.response = compressed_result
        obj.response_code = response_code
        obj.save(update_fields=["job_status", "response", "response_code"])
        mark_completed(queue_task_uuid)

    except MindoffValidationError as exc:
        if exc.code == "QUEUE_TASK_CANCELLED":
            _mark_db_cancelled(obj, queue_task_uuid)
            return

        tb = traceback.format_exc()
        obj.job_status = "failed"
        obj.error = _compress_json_result_if_large(
            {"message": str(exc), "traceback": tb, "code": exc.code},
            _get_json_compression_min_bytes("error", default=4096),
        )
        obj.response_code = int(
            MINDOFF_RESPONSES.get(exc.code, {}).get("http_status") or 400
        )
        obj.save(update_fields=["job_status", "error", "response_code"])
        mark_failed(queue_task_uuid, error=str(exc))

    except Exception as exc:
        tb = traceback.format_exc()
        obj.job_status = "failed"
        obj.error = _compress_json_result_if_large(
            {"message": str(exc), "traceback": tb},
            _get_json_compression_min_bytes("error", default=4096),
        )
        obj.response_code = 500
        obj.save(update_fields=["job_status", "error", "response_code"])
        mark_failed(queue_task_uuid, error=str(exc))


def dramatiq_healthcheck(probe_id: str) -> bool:
    """Check whether at least one Dramatiq worker is alive by probing the broker."""
    try:
        broker = dramatiq.get_broker()
        return bool(getattr(broker, "workers", set()))
    except Exception:
        return False


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
    """
    redis_state = _safe_get_queue_status(queue_task_uuid)
    obj = MOQueue.objects.filter(id=queue_task_uuid).first()

    if obj is None and _state_status(redis_state) == "unknown":
        return {"job_status": "unknown", "is_cancel": False}
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
        _sync_db_from_redis(obj, redis_state)
        obj.refresh_from_db()
        return _safe_get_queue_status(str(obj.id))

    _sync_redis_from_db(obj)
    return _safe_get_queue_status(str(obj.id))


def cancel_queue_task(queue_task_uuid: str) -> str:
    """
    Request cancellation of a queued or running task.

    Returns one of: ``"cancelled"`` | ``"not_found"`` | ``"not_cancellable"``
    """
    obj = MOQueue.objects.filter(id=queue_task_uuid).first()
    if not obj:
        return "not_found"

    redis_state = _safe_get_queue_status(queue_task_uuid)
    if _state_status(redis_state) == "unknown":
        redis_state = rehydrate_queue_state(queue_task_uuid)

    status = _state_status(redis_state)

    # Cross-check DB to guard against stale Redis
    db_status = obj.job_status
    effective_status = status if status != "unknown" else db_status

    if effective_status in ("completed", "failed", "cancelled"):
        return "not_cancellable"

    if effective_status not in ("queued", "running"):
        return "not_cancellable"

    mark_cancel_requested(str(obj.id))
    return "cancelled"


def retry_failed_queue_task(queue_task_uuid: str) -> str:
    """
    Re-enqueue a previously failed or cancelled task from scratch.

    Returns one of: ``"queued"`` | ``"not_found"`` | ``"not_retriable"``
    """
    obj = MOQueue.objects.filter(id=queue_task_uuid).first()
    if not obj:
        return "not_found"

    if obj.job_status not in ("failed", "cancelled"):
        return "not_retriable"

    # Clear all terminal state including response_code so the new run starts clean
    obj.job_status = "queued"
    obj.error = None
    obj.response = None
    obj.response_code = None
    obj.save(
        update_fields=["job_status", "error", "response", "response_code", "updated_at"]
    )

    init_queue(
        queue_task_uuid=str(obj.id),
        created_at=timezone.now(),
    )
    execute_queue.send(str(obj.id))
    return "queued"


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────


def _extract_response_code(result) -> int:
    if isinstance(result, DRFResponse):
        return int(result.status_code)
    if isinstance(result, JsonResponse):
        return int(result.status_code)
    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, dict):
            code = message.get("code")
            if isinstance(code, str):
                mapped = MINDOFF_RESPONSES.get(code, {}).get("http_status")
                if isinstance(mapped, int):
                    return mapped
    return 200


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
        "codec": _json_compression_string,
        "data": b64,
    }


def _compress_json_result_if_large(result, min_bytes):
    if result is None:
        return None
    if _is_compressed_json_blob(result):
        return result

    payload = json.dumps(result, separators=(",", ":"), ensure_ascii=True, default=str)
    try:
        threshold = max(0, int(min_bytes))
    except (TypeError, ValueError):
        threshold = 0

    if len(payload.encode("utf-8")) < threshold:
        return result

    compressed = gzip.compress(payload.encode("utf-8"))
    b64 = base64.b64encode(compressed).decode("ascii")
    return {
        _COMPRESSED_RESPONSE_FLAG: True,
        "codec": _json_compression_string,
        "data": b64,
    }


def _decode_json_blob(payload):
    if not _is_compressed_json_blob(payload):
        return payload
    raw_b64 = payload.get("data")
    if not raw_b64:
        return payload
    try:
        raw = gzip.decompress(base64.b64decode(raw_b64)).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return payload


def _is_compressed_json_blob(payload) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get(_COMPRESSED_RESPONSE_FLAG) is True
        and payload.get("codec") == _json_compression_string
    )


def _get_json_compression_min_bytes(field_name: str, default: int) -> int:
    setting_name = f"MINDOFF_QUEUE_{str(field_name).upper()}_COMPRESS_MIN_BYTES"
    raw = getattr(settings, setting_name, default)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, int(default))


def _extract_result(result):
    if isinstance(result, DRFResponse):
        return result.data
    if isinstance(result, JsonResponse):
        return json.loads(result.content)
    return result


def _mark_db_cancelled(obj: MOQueue, queue_task_uuid: str):
    cancelled_result = _extract_result(
        mo_response_kit.json_response(
            code="QUEUE_TASK_CANCELLED",
            category="warning",
            data={"queue_id": str(queue_task_uuid), "job_status": "cancelled"},
        )
    )
    obj.job_status = "cancelled"
    obj.response = _compress_json_result_if_large(
        cancelled_result,
        _get_json_compression_min_bytes("response", default=0),
    )
    obj.response_code = int(
        MINDOFF_RESPONSES.get("QUEUE_TASK_CANCELLED", {}).get("http_status") or 200
    )
    obj.error = None
    obj.save(update_fields=["job_status", "response", "response_code", "error"])
    mark_cancelled(str(queue_task_uuid))


def _safe_get_queue_status(queue_task_uuid: str) -> dict:
    try:
        return get_queue_status(queue_task_uuid)
    except Exception:
        return {"job_status": "unknown", "is_cancel": False}


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
    status = obj.job_status
    if status == "queued":
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
    elif status == "running":
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
        mark_running(str(obj.id))
    elif status == "completed":
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
        mark_completed(str(obj.id))
    elif status == "failed":
        decoded_error = _decode_json_blob(obj.error) or {}
        if not isinstance(decoded_error, dict):
            decoded_error = {}
        error_message = str(decoded_error.get("message", "failed"))
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
        mark_failed(str(obj.id), error=error_message)
    elif status == "cancelled":
        init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
        mark_cancelled(str(obj.id))


def _sync_db_from_redis(obj: MOQueue, redis_state: dict):
    if _is_cancel_requested(redis_state):
        _mark_db_cancelled(obj, str(obj.id))
        return

    status = _state_status(redis_state)
    if status == "unknown":
        return
    if status == "cancelled":
        _mark_db_cancelled(obj, str(obj.id))
        return
    if obj.job_status == status:
        return

    obj.job_status = status
    if status in ("queued", "running", "completed"):
        obj.error = None
    if status == "failed":
        message = str(redis_state.get("progress", {}).get("current_message", "failed"))
        obj.error = _compress_json_result_if_large(
            {"message": message},
            _get_json_compression_min_bytes("error", default=4096),
        )
    obj.save(update_fields=["job_status", "error", "updated_at"])
