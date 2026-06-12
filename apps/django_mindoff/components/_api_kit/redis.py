import logging
import redis
import json
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

TTL_QUEUED = 60 * 60 * 24  # 24h
TTL_COMPLETED = 60 * 60 * 1  # 1h
TTL_FAILED = 60 * 60 * 6  # 6h
TTL_CANCELLED = 60 * 60 * 6  # 6h
SSE_TTL = 60 * 60 * 1  # 1h

redis_client = redis.Redis.from_url(settings.REDIS_URL)


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────


def _now() -> str:
    return timezone.now().isoformat()


def _status_of(state: dict) -> str:
    return str(state.get("job_status") or "")


def _is_cancel_of(state: dict) -> bool:
    return str(state.get("is_cancel", "false")).lower() == "true"


def _progress_json(percent: int, step: str, message: str) -> str:
    try:
        clamped = max(0, min(100, int(percent)))
    except (TypeError, ValueError):
        clamped = 0
    return json.dumps(
        {
            "percent": clamped,
            "current_step": step or "",
            "current_message": message or "",
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _normalize_state(state: dict) -> dict:
    status = _status_of(state) or "unknown"

    raw_progress = state.get("progress") or {}
    if isinstance(raw_progress, str):
        try:
            raw_progress = json.loads(raw_progress)
        except (TypeError, ValueError):
            raw_progress = {}

    percent = raw_progress.get("percent", 0)
    try:
        percent = max(0, min(100, int(percent)))
    except (TypeError, ValueError):
        percent = 0

    updated_at = state.get("updated_at") or _now()
    started_at = state.get("started_at") or updated_at

    normalized = {
        "id": str(state.get("id", "")),
        "job_status": status,
        "is_cancel": _is_cancel_of(state),
        "progress": {
            "percent": percent,
            "current_step": str(raw_progress.get("current_step", "")),
            "current_message": str(raw_progress.get("current_message", "")),
        },
        "started_at": str(started_at),
        "updated_at": str(updated_at),
    }

    return normalized


# ─────────────────────────────────────────────
# Public state-transition functions
# ─────────────────────────────────────────────


def init_queue(queue_task_uuid: str, created_at):
    """
    Initialise a fresh queue entry in Redis.

    Stores only runtime queue state (no API metadata).
    """
    key = f"moq:{queue_task_uuid}"
    ts = created_at.isoformat()

    mapping = {
        "id": str(queue_task_uuid),
        "job_status": "queued",
        "is_cancel": "false",
        "progress": _progress_json(0, "", "queued"),
        "started_at": ts,
        "updated_at": ts,
    }
    redis_client.hset(key, mapping=mapping)
    redis_client.expire(key, TTL_QUEUED)


def mark_running(queue_task_uuid: str):
    """
    Update the in-flight progress of a running task.
    """
    key = f"moq:{queue_task_uuid}"
    state = get_queue_status(queue_task_uuid)

    progress = state.get("progress") or {}
    mapping = {
        "id": str(queue_task_uuid),
        "job_status": "running",
        # preserve is_cancel flag — do NOT clear it here
        "is_cancel": "true" if state.get("is_cancel") else "false",
        "progress": _progress_json(
            progress.get("percent", 0),
            progress.get("current_step", ""),
            progress.get("current_message", "running"),
        ),
        "started_at": state.get("started_at") or _now(),
        "updated_at": _now(),
    }
    redis_client.hset(key, mapping=mapping)
    # Running tasks must never expire while the worker is active
    redis_client.persist(key)


def update_progress(
    queue_task_uuid: str,
    *,
    progress: int,
    step: str | None = None,
    message: str | None = None,
):
    """
    Update the in-flight progress of a running task.

    ``step`` should be the human-readable label (already resolved by
    ``progress_checkpoint``).  ``message`` is an optional free-text override
    shown to the caller.
    """
    key = f"moq:{queue_task_uuid}"
    state = get_queue_status(queue_task_uuid)

    current_progress = state.get("progress") or {}
    mapping = {
        "id": str(queue_task_uuid),
        # Preserve is_cancel — this is the ONLY place a worker writes progress;
        # we must never accidentally clear a pending cancel request.
        "is_cancel": "true" if state.get("is_cancel") else "false",
        "progress": _progress_json(
            progress,
            step if step is not None else str(current_progress.get("current_step", "")),
            (
                message
                if message is not None
                else str(current_progress.get("current_message", ""))
            ),
        ),
        "started_at": state.get("started_at") or _now(),
        "updated_at": _now(),
    }
    redis_client.hset(key, mapping=mapping)


def mark_completed(queue_task_uuid: str):
    """Mark a queue task as completed."""
    key = f"moq:{queue_task_uuid}"
    state = get_queue_status(queue_task_uuid)

    redis_client.hset(
        key,
        mapping={
            "id": str(queue_task_uuid),
            "job_status": "completed",
            "is_cancel": "false",
            "progress": _progress_json(100, "", "completed"),
            "started_at": state.get("started_at") or _now(),
            "updated_at": _now(),
        },
    )
    redis_client.expire(key, TTL_COMPLETED)


def mark_failed(queue_task_uuid: str, error: str):
    """Mark a queue task as failed with an error message."""
    key = f"moq:{queue_task_uuid}"
    state = get_queue_status(queue_task_uuid)

    progress = state.get("progress") or {}
    redis_client.hset(
        key,
        mapping={
            "id": str(queue_task_uuid),
            "job_status": "failed",
            "is_cancel": "false",
            "progress": _progress_json(
                progress.get("percent", 0),
                progress.get("current_step", ""),
                error,
            ),
            "started_at": state.get("started_at") or _now(),
            "updated_at": _now(),
        },
    )
    redis_client.expire(key, TTL_FAILED)


def mark_cancelled(queue_task_uuid: str, message: str = "cancelled"):
    """Mark a queue task as cancelled, optionally with a custom message."""
    key = f"moq:{queue_task_uuid}"
    state = get_queue_status(queue_task_uuid)

    progress = state.get("progress") or {}
    redis_client.hset(
        key,
        mapping={
            "id": str(queue_task_uuid),
            "job_status": "cancelled",
            "is_cancel": "true",
            "progress": _progress_json(
                progress.get("percent", 0),
                progress.get("current_step", ""),
                message,
            ),
            "started_at": state.get("started_at") or _now(),
            "updated_at": _now(),
        },
    )
    redis_client.expire(key, TTL_CANCELLED)


def mark_cancel_requested(queue_task_uuid: str):
    """
    Signal to a running worker that it should stop at its next
    ``progress_checkpoint`` call.  We only flip ``is_cancel``; the
    ``job_status`` stays as-is so the worker's own transition to
    ``"cancelled"`` is the single source of truth.
    """
    key = f"moq:{queue_task_uuid}"
    state = get_queue_status(queue_task_uuid)

    # If key has already expired (worker finished), nothing to do.
    if _status_of(state) == "unknown":
        return

    redis_client.hset(
        key,
        mapping={
            "is_cancel": "true",
            "updated_at": _now(),
        },
    )
    # Keep the key alive; the worker (or rehydration) will clean it up.
    redis_client.persist(key)


def get_queue_status(queue_task_uuid: str) -> dict:
    """Return the current status of a queue task as a JSON blob.

    See the response format in the documentation for `enqueue_process`.
    """
    key = f"moq:{queue_task_uuid}"
    data = redis_client.hgetall(key)

    if not data:
        return {"job_status": "unknown", "is_cancel": False}

    decoded = {k.decode(): v.decode() for k, v in data.items()}

    # Deserialise the progress JSON blob
    progress_raw = decoded.get("progress", "")
    if progress_raw:
        try:
            decoded["progress"] = json.loads(progress_raw)
        except (TypeError, ValueError):
            decoded["progress"] = {}
    else:
        decoded["progress"] = {}

    decoded["id"] = decoded.get("id", str(queue_task_uuid))
    return _normalize_state(decoded)


# ─────────────────────────────────────────────
# SSE slot management
# ─────────────────────────────────────────────


def acquire_sse_slot(user_id, *, limit: int) -> bool:
    """Acquire a SSE slot for the given user.

    Returns True if a slot is available, False otherwise.
    """
    key = sse_key(user_id)
    count = redis_client.incr(key)
    redis_client.expire(key, SSE_TTL)
    return count <= limit


def release_sse_slot(user_id):
    """Release a previously acquired SSE slot for the given user."""
    key = sse_key(user_id)
    try:
        redis_client.decr(key)
    except redis.RedisError as exc:
        logger.debug("Failed to release SSE slot for user %s: %s", user_id, exc)


# ─────────────────────────────────────────────
# Helpers for SSE views
# ─────────────────────────────────────────────


def sse_event(data: dict) -> str:
    """Return a Server-Sent Event formatted string from the given data."""
    return f"data: {json.dumps(data)}\n\n"


def sse_key(user_id) -> str:
    """Return the SSE key for the given user_id."""
    return f"sse:active:{user_id}"
