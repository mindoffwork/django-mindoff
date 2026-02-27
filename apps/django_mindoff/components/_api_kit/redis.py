import redis
import json
from django.conf import settings
from django.utils import timezone
from asgiref.sync import async_to_sync

TTL_QUEUED = 60 * 60 * 24  # 24h
TTL_COMPLETED = 60 * 60 * 1  # 1h
TTL_FAILED = 60 * 60 * 6  # 6h
SSE_TTL = 60 * 60 * 1  # 1h
redis_client = redis.Redis.from_url(settings.REDIS_URL)


def _now():
    return timezone.now().isoformat()


def init_queue(queue_task_uuid: str, created_at):
    key = f"moq:{queue_task_uuid}"

    redis_client.hset(
        key,
        mapping={
            "status": "queued",
            "progress": 0,
            "current_step": "",
            "message": "queued",
            "last_updated_at": created_at.isoformat(),
        },
    )
    redis_client.expire(key, TTL_QUEUED)


def mark_running(queue_task_uuid: str):
    key = f"moq:{queue_task_uuid}"

    redis_client.hset(
        key,
        mapping={
            "status": "running",
            "last_updated_at": _now(),
        },
    )
    redis_client.persist(key)


def update_progress(queue_task_uuid: str, *, progress, step=None, message=None):
    key = f"moq:{queue_task_uuid}"

    mapping = {
        "progress": max(0, min(100, int(progress))),
        "last_updated_at": _now(),
    }

    if step is not None:
        mapping["current_step"] = step
    if message is not None:
        mapping["message"] = message

    redis_client.hset(key, mapping=mapping)


def mark_completed(queue_task_uuid: str):
    key = f"moq:{queue_task_uuid}"

    redis_client.hset(
        key,
        mapping={
            "status": "completed",
            "progress": 100,
            "message": "completed",
            "last_updated_at": _now(),
        },
    )
    redis_client.expire(key, TTL_COMPLETED)


def mark_failed(queue_task_uuid: str, error: str):
    key = f"moq:{queue_task_uuid}"

    redis_client.hset(
        key,
        mapping={
            "status": "failed",
            "message": error,
            "last_updated_at": _now(),
        },
    )
    redis_client.expire(key, TTL_FAILED)


def mark_cancelled(queue_task_uuid: str, message: str = "cancelled"):
    key = f"moq:{queue_task_uuid}"

    redis_client.hset(
        key,
        mapping={
            "status": "cancelled",
            "message": message,
            "last_updated_at": _now(),
        },
    )
    redis_client.expire(key, TTL_FAILED)


def get_queue_status(queue_task_uuid: str) -> dict:
    key = f"moq:{queue_task_uuid}"
    data = redis_client.hgetall(key)

    if not data:
        return {"status": "unknown"}

    return {k.decode(): v.decode() for k, v in data.items()}


def acquire_sse_slot(user_id, *, limit: int) -> bool:
    key = sse_key(user_id)
    count = redis_client.incr(key)
    redis_client.expire(key, SSE_TTL)

    return count <= limit


def release_sse_slot(user_id):
    key = sse_key(user_id)
    try:
        redis_client.decr(key)
    except redis.RedisError:
        pass


# ------- Helper Functions --------


def sse_event(data: dict):
    return f"data: {json.dumps(data)}\n\n"


def sse_key(user_id):
    return f"sse:active:{user_id}"
