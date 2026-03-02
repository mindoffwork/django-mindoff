import json
import time
import gzip
import base64

from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.urls import reverse
from typing import Literal

from .models import MOQueue
from .components._api_kit.redis import (
    get_queue_status,
    sse_event,
    acquire_sse_slot,
    release_sse_slot,
)
from .components._api_kit.queue_process import (
    rehydrate_queue_state,
    cancel_queue_task,
    retry_failed_queue_task,
)
from django_ratelimit.core import is_ratelimited
from rest_framework.renderers import BaseRenderer
from .components.validation_kit import mo_validation_kit
from .components.helper_kit import get_api_class_from_url_name
from .components.api_kit import MindoffAPIMixin
from .components.response_kit import mo_response_kit

access_denied_message = "User not allowed to access this queue task"


class SSEEventStreamRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "event-stream"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


# ─────────────────────────────────────────────────────────────────────────────
# Queue status (polling)
# ─────────────────────────────────────────────────────────────────────────────


class MindoffQueueStatusView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_status"
    api_name: str = "Mindoff Queue Status"
    api_description: str = "Get the status of a specific queue task"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = "120/m"

    def run(self, request, queue_task_uuid):

        # 1. Load task (SQL is the authoritative store)
        try:
            obj = MOQueue.objects.get(id=queue_task_uuid)
        except MOQueue.DoesNotExist:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_NOT_FOUND",
                category="warning",
                data={"queue_task_uuid": queue_task_uuid},
            )

        # 2. Per-task rate limiting (uses the originating API's configured limit)
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        queue_status_limit = getattr(api, "queue_status_limit", None)
        if queue_status_limit:
            limited = is_ratelimited(
                request,
                group=f"{api.api_url_name}_{queue_task_uuid}",
                key="user_or_ip",
                rate=queue_status_limit,
                increment=True,
            )
            mo_validation_kit.ensure_falsey(
                limited,
                msg="Rate limit exceeded. Please try again later.",
                code="API_RATE_LIMITED",
            )

        # 3. Ownership check
        if obj.user_ref_id:
            request_user_id = getattr(request.user, "id", None)
            if str(obj.user_ref_id) != str(request_user_id):
                return mo_response_kit.json_response(
                    code="PERMISSION_DENIED", category="danger"
                )

        # 4. Fetch live Redis state (rehydrate if expired)
        redis_state = get_queue_status(queue_task_uuid)
        if _state_status(redis_state) == "unknown":
            redis_state = rehydrate_queue_state(str(queue_task_uuid))

        if _state_status(redis_state) != "unknown":
            data = {
                "queue_id": str(queue_task_uuid),
                **_status_payload(redis_state),
                "response": _get_queue_response(obj),
            }
            return mo_response_kit.json_response(
                code="SUCCESS", category="success", data=data
            )

        # 5. Fallback: Redis fully expired and rehydration failed → use DB
        data = {
            "queue_id": str(queue_task_uuid),
            "job_status": obj.status,
            "is_cancel": obj.status == "cancelled",
            "progress": {
                "percent": 100 if obj.status == "completed" else 0,
                "current_step": "",
                "current_message": str(obj.error or ""),
            },
            "steps": _resolve_steps_from_api(obj.api_url_name),
            "started_at": obj.created_at.isoformat(),
            "updated_at": obj.updated_at.isoformat(),
            "response": _get_queue_response(obj),
        }
        return mo_response_kit.json_response(
            code="SUCCESS", category="success", data=data
        )


# ─────────────────────────────────────────────────────────────────────────────
# Queue status stream (SSE)
# ─────────────────────────────────────────────────────────────────────────────


class MindoffQueueStatusStreamView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_status_stream"
    api_name: str = "Mindoff Queue Status Stream"
    api_description: str = "Real-time queue task status via Server-Sent Events"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = "60/m"
    renderer_classes = [SSEEventStreamRenderer]

    def run(self, request, queue_task_uuid):
        try:
            obj = MOQueue.objects.get(id=queue_task_uuid)
        except MOQueue.DoesNotExist:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_NOT_FOUND",
                category="warning",
                data={"queue_task_uuid": queue_task_uuid},
            )

        self._validate_user(request, obj)
        self._acquire_sse_limit(obj)

        response = StreamingHttpResponse(
            self._event_stream(queue_task_uuid, obj),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _validate_user(self, request, obj):
        if not obj.user_ref_id:
            return
        request_user_id = getattr(request.user, "id", None)
        mo_validation_kit.ensure_equal(
            str(obj.user_ref_id),
            str(request_user_id),
            msg=access_denied_message,
            code="PERMISSION_DENIED",
        )

    def _acquire_sse_limit(self, obj):
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        max_streams = getattr(api, "queue_status_stream_limit", None)
        if max_streams is None:
            return
        mo_validation_kit.ensure_truthy(
            acquire_sse_slot(obj.owner_id, limit=max_streams),
            msg="Too many active streams",
            code="RATE_LIMITED",
        )

    def _event_stream(self, queue_task_uuid, obj):
        def generator():
            last_state = None
            try:
                while True:
                    state = get_queue_status(queue_task_uuid)
                    if _state_status(state) == "unknown":
                        state = rehydrate_queue_state(str(queue_task_uuid))
                        if _state_status(state) == "unknown":
                            yield sse_event(self._fallback_state(obj))
                            return

                    event = self._maybe_sse_event(state, last_state)
                    if event is not None:
                        yield event
                        last_state = _stream_state_payload(state)

                    if _state_status(state) in ("completed", "failed", "cancelled"):
                        return

                    time.sleep(1)
            finally:
                release_sse_slot(obj.owner_id)

        return generator()

    def _fallback_state(self, obj):
        return {
            "id": str(obj.id),
            "job_status": obj.status,
            "is_cancel": obj.status == "cancelled",
            "progress": {
                "percent": 100 if obj.status == "completed" else 0,
                "current_step": "",
                "current_message": str(obj.error or ""),
            },
            "steps": _resolve_steps_from_api(obj.api_url_name),
            "started_at": obj.created_at.isoformat(),
            "updated_at": obj.updated_at.isoformat(),
        }

    def _maybe_sse_event(self, state, last_state):
        event_state = _stream_state_payload(state)
        if event_state == last_state:
            return None
        return sse_event(event_state)


# ─────────────────────────────────────────────────────────────────────────────
# Queue cancel
# ─────────────────────────────────────────────────────────────────────────────


class MindoffQueueCancelView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_cancel"
    api_name: str = "Mindoff Queue Cancel"
    api_description: str = "Cancel a running or queued task"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "post"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = "60/m"

    def run(self, request, queue_task_uuid):
        try:
            obj = MOQueue.objects.get(id=queue_task_uuid)
        except MOQueue.DoesNotExist:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_NOT_FOUND",
                category="warning",
                data={"queue_task_uuid": queue_task_uuid},
            )

        self._validate_user(request, obj)

        result = cancel_queue_task(str(queue_task_uuid))
        if result == "cancelled":
            return mo_response_kit.json_response(
                code="SUCCESS",
                category="success",
                data={"queue_id": str(queue_task_uuid), "status": "cancel_requested"},
            )

        return mo_response_kit.json_response(
            code="QUEUE_TASK_NOT_CANCELLABLE",
            category="warning",
            data={"queue_id": str(queue_task_uuid), "status": obj.status},
        )

    def _validate_user(self, request, obj):
        if not obj.user_ref_id:
            return
        request_user_id = getattr(request.user, "id", None)
        mo_validation_kit.ensure_equal(
            str(obj.user_ref_id),
            str(request_user_id),
            msg=access_denied_message,
            code="PERMISSION_DENIED",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Queue retry
# ─────────────────────────────────────────────────────────────────────────────


class MindoffQueueRetryView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_retry"
    api_name: str = "Mindoff Queue Retry"
    api_description: str = "Retry a previously failed queue task"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "post"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = "120/m"

    def run(self, request, queue_task_uuid):
        try:
            obj = MOQueue.objects.get(id=queue_task_uuid)
        except MOQueue.DoesNotExist:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_NOT_FOUND",
                category="warning",
                data={"queue_task_uuid": queue_task_uuid},
            )

        self._validate_user(request, obj)

        result = retry_failed_queue_task(str(queue_task_uuid))
        if result == "queued":
            status_url = request.build_absolute_uri(
                reverse("mo_queue_status", args=[queue_task_uuid])
            )
            status_stream_url = request.build_absolute_uri(
                reverse("mo_queue_status_stream", args=[queue_task_uuid])
            )
            return mo_response_kit.json_response(
                code="QUEUED",
                category="success",
                data={
                    "queue_id": str(queue_task_uuid),
                    "status_url": status_url,
                    "status_stream_url": status_stream_url,
                },
            )

        return mo_response_kit.json_response(
            code="QUEUE_TASK_NOT_RETRIABLE",
            category="warning",
            data={"queue_id": str(queue_task_uuid), "status": obj.status},
        )

    def _validate_user(self, request, obj):
        if not obj.user_ref_id:
            return
        request_user_id = getattr(request.user, "id", None)
        mo_validation_kit.ensure_equal(
            str(obj.user_ref_id),
            str(request_user_id),
            msg=access_denied_message,
            code="PERMISSION_DENIED",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_queue_response(obj: MOQueue) -> dict:
    """Decompress and return the stored response payload, or ``{}``."""
    payload = obj.response or {}
    if not isinstance(payload, dict):
        return {}

    if payload.get("__compressed__") and payload.get("codec") == "gzip+base64":
        raw_b64 = payload.get("data")
        if not raw_b64:
            return {}
        try:
            compressed = base64.b64decode(raw_b64)
            raw = gzip.decompress(compressed).decode("utf-8")
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {"result": decoded}
        except Exception:
            return {}

    return payload


def _state_status(state: dict) -> str:
    return state.get("job_status") or "unknown"


def _status_payload(state: dict) -> dict:
    """
    Build the status dict that is embedded in the polling response.
    Includes ``steps`` so the client can render a progress timeline.
    """
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    return {
        "id": str(state.get("id", "")),
        "job_status": _state_status(state),
        "is_cancel": bool(state.get("is_cancel", False)),
        "progress": {
            "percent": max(0, min(100, int(progress.get("percent", 0)))),
            "current_step": str(progress.get("current_step", "")),
            "current_message": str(progress.get("current_message", "")),
        },
        "steps": state.get("steps") or {},
        "started_at": state.get("started_at"),
        "updated_at": state.get("updated_at"),
    }


def _stream_state_payload(state: dict) -> dict:
    """Identical to ``_status_payload`` but also used for SSE deduplication."""
    return _status_payload(state)


def _resolve_steps_from_api(api_url_name: str) -> dict:
    """
    Look up ``progress_steps`` from the API class when Redis has expired and
    we need to include steps in a fallback response.
    """
    try:
        api_cls = get_api_class_from_url_name(api_url_name=api_url_name)
        return getattr(api_cls, "progress_steps", None) or {}
    except Exception:
        return {}
