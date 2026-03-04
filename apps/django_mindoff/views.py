import json
import time
import gzip
import base64

from django.http import StreamingHttpResponse
from django.urls import reverse
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db import models
from typing import Literal

from rest_framework.response import Response
from rest_framework.renderers import BaseRenderer

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
from .components.validation_kit import mo_validation_kit
from .components.helper_kit import get_api_class_from_url_name
from .components.api_kit import MindoffAPIMixin
from .components.response_kit import mo_response_kit

access_denied_message = "User not allowed to access this queue task"
rate_limit_exceeded_message = "Rate limit exceeded. Please try again later."
api_request_limit_120 = "120/m"


class SSEEventStreamRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "event-stream"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class MindoffQueueDetailView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_detail"
    api_name: str = "Mindoff Queue Detail"
    api_description: str = "Retrieve the result (or current status) of a queue task"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = api_request_limit_120

    def run(self, request, queue_task_uuid):
        try:
            obj = MOQueue.objects.get(id=queue_task_uuid)
        except MOQueue.DoesNotExist:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_NOT_FOUND",
                category="warning",
                data={"queue_task_uuid": str(queue_task_uuid)},
            )

        self._apply_rate_limit(request, obj, queue_task_uuid)
        _check_ownership(request, obj)

        return self._response_for_status(obj, queue_task_uuid)

    def _apply_rate_limit(self, request, obj, queue_task_uuid):
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        if not _is_queue_mode_api(api):
            return
        limit = getattr(api, "queue_detail_api_limit", None)
        if not limit:
            return
        limited = is_ratelimited(
            request,
            group=f"{api.api_url_name}_{queue_task_uuid}",
            key="user_or_ip",
            rate=limit,
            increment=True,
        )
        mo_validation_kit.ensure_falsey(
            limited,
            msg=rate_limit_exceeded_message,
            code="API_RATE_LIMITED",
        )

    def _response_for_status(self, obj, queue_task_uuid):
        queue_id = str(queue_task_uuid)
        dispatch = {
            "completed": self._completed_response,
            "failed": self._failed_response,
            "cancelled": self._cancelled_response,
            "pending": self._pending_response,
            "running": self._running_response,
        }
        handler = dispatch.get(obj.job_status, self._unknown_response)
        return handler(obj, queue_id)

    def _completed_response(self, obj, queue_id):
        response_json = _get_queue_response(obj)
        response_json, embedded_status = _normalize_completed_response(response_json)
        if not response_json:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_UNKNOWN_STATUS",
                category="warning",
                data={
                    "queue_id": queue_id,
                    "job_status": obj.job_status,
                },
            )
        return Response(
            response_json,
            status=_response_code_to_http_status(
                obj.response_code,
                default=embedded_status or 200,
            ),
        )

    def _failed_response(self, obj, queue_id):
        payload = _get_queue_response(obj)
        decoded_error = _maybe_decode_compressed_json(obj.error)
        payload_data = payload or {"error": decoded_error}
        if not isinstance(payload_data, dict):
            payload_data = {"result": payload_data}
        payload_data.setdefault("queue_id", queue_id)
        payload_data.setdefault("job_status", obj.job_status)
        return mo_response_kit.json_response(
            code="QUEUE_TASK_FAILED",
            category="danger",
            data=payload_data,
        )

    def _cancelled_response(self, obj, queue_id):
        return mo_response_kit.json_response(
            code="QUEUE_TASK_CANCELLED",
            category="warning",
            data={
                "queue_id": queue_id,
                "job_status": obj.job_status,
            },
        )

    def _pending_response(self, obj, queue_id):
        return mo_response_kit.json_response(
            code="QUEUE_TASK_PENDING",
            category="info",
            data={
                "queue_id": queue_id,
                "job_status": obj.job_status,
            },
        )

    def _running_response(self, obj, queue_id):
        return mo_response_kit.json_response(
            code="QUEUE_TASK_RUNNING",
            category="info",
            data={
                "queue_id": queue_id,
                "job_status": obj.job_status,
                "progress": _get_live_progress(queue_id),
            },
        )

    def _unknown_response(self, obj, queue_id):
        return mo_response_kit.json_response(
            code="QUEUE_TASK_UNKNOWN_STATUS",
            category="warning",
            data={
                "queue_id": queue_id,
                "job_status": obj.job_status,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Queue list  GET queue/list/
# ─────────────────────────────────────────────────────────────────────────────


class MindoffQueueListView(MindoffAPIMixin):
    """
    GET queue/list/

    Query params (all optional, combinable):
      ?id=<uuid>
      ?job_status=<status>
      ?user_ref_id=<uuid>
      ?owner_id=<str>
      ?api_url_name=<str>
      ?page=<int>
      ?page_size=<int>
    """

    api_url_name: str = "mo_queue_list"
    api_name: str = "Mindoff Queue List"
    api_description: str = "List queue tasks with optional filtering"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = None

    def _initial_validate_api_rate_limit(self, request):
        self.api_request_limit = getattr(
            settings, "MINDOFF_QUEUE_LIST_API_REQUEST_LIMIT", api_request_limit_120
        )
        super()._initial_validate_api_rate_limit(request)

    def run(self, request):
        qs = MOQueue.objects.all().order_by("-created_at")

        # ── Filters ──────────────────────────────────────────────────────────
        task_id = request.GET.get("id")
        job_status = request.GET.get("job_status")
        user_ref_id = request.GET.get("user_ref_id")
        owner_id = request.GET.get("owner_id")
        api_url_name = request.GET.get("api_url_name")

        if task_id:
            qs = qs.filter(id=task_id)
        if job_status:
            qs = qs.filter(job_status=job_status)
        if user_ref_id:
            qs = qs.filter(user_ref_id=user_ref_id)
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        if api_url_name:
            qs = qs.filter(api_url_name=api_url_name)

        page = _safe_int(request.GET.get("page"), default=1, minimum=1)
        page_size = _safe_int(request.GET.get("page_size"), default=50, minimum=1)
        page_size = min(page_size, 200)
        paginator = Paginator(qs, page_size)
        total_count = paginator.count
        total_pages = paginator.num_pages

        try:
            page_obj = paginator.page(page)
        except EmptyPage:
            page = total_pages if total_pages > 0 else 1
            page_obj = paginator.page(page) if total_pages > 0 else []

        tasks = (
            [_serialize_queue_obj(obj) for obj in page_obj.object_list]
            if total_pages > 0
            else []
        )

        return mo_response_kit.json_response(
            code="SUCCESS",
            category="success",
            data={
                "tasks": tasks,
                "count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": bool(getattr(page_obj, "has_next", lambda: False)()),
                "has_previous": bool(
                    getattr(page_obj, "has_previous", lambda: False)()
                ),
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Queue status stream (SSE)  GET queue/<job_uuid>/stream/
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
                data={"queue_task_uuid": str(queue_task_uuid)},
            )

        _check_ownership(request, obj)
        self._acquire_sse_limit(obj)

        response = StreamingHttpResponse(
            self._event_stream(queue_task_uuid, obj),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _acquire_sse_limit(self, obj):
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        if not _is_queue_mode_api(api):
            return
        max_streams = getattr(api, "queue_status_stream_api_limit", None)
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
            "job_status": obj.job_status,
            "is_cancel": obj.job_status == "cancelled",
            "progress": {
                "percent": 100 if obj.job_status == "completed" else 0,
                "current_step": "",
                "current_message": str(obj.error or ""),
            },
            "started_at": obj.created_at.isoformat(),
            "updated_at": obj.updated_at.isoformat(),
        }

    def _maybe_sse_event(self, state, last_state):
        event_state = _stream_state_payload(state)
        if event_state == last_state:
            return None
        return sse_event(event_state)


# ─────────────────────────────────────────────────────────────────────────────
# Queue cancel  POST queue/<job_uuid>/cancel/
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
                data={"queue_task_uuid": str(queue_task_uuid)},
            )

        _check_ownership(request, obj)
        self._apply_cancel_rate_limit(request, obj, queue_task_uuid)

        result = cancel_queue_task(str(queue_task_uuid))
        if result == "cancelled":
            return mo_response_kit.json_response(
                code="SUCCESS",
                category="success",
                data={
                    "queue_id": str(queue_task_uuid),
                    "job_status": "cancel_requested",
                },
            )

        return mo_response_kit.json_response(
            code="QUEUE_TASK_NOT_CANCELLABLE",
            category="warning",
            data={
                "queue_id": str(queue_task_uuid),
                "job_status": obj.job_status,
            },
        )

    def _apply_cancel_rate_limit(self, request, obj, queue_task_uuid):
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        if not _is_queue_mode_api(api):
            return
        limit = getattr(api, "queue_cancel_api_limit", None)
        if not limit:
            return
        limited = is_ratelimited(
            request,
            group=f"{api.api_url_name}_{queue_task_uuid}_cancel",
            key="user_or_ip",
            rate=limit,
            increment=True,
        )
        mo_validation_kit.ensure_falsey(
            limited,
            msg=rate_limit_exceeded_message,
            code="API_RATE_LIMITED",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Queue retry  POST queue/<job_uuid>/retry/
# ─────────────────────────────────────────────────────────────────────────────


class MindoffQueueRetryView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_retry"
    api_name: str = "Mindoff Queue Retry"
    api_description: str = "Retry a previously failed queue task"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "post"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = api_request_limit_120

    def run(self, request, queue_task_uuid):
        try:
            obj = MOQueue.objects.get(id=queue_task_uuid)
        except MOQueue.DoesNotExist:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_NOT_FOUND",
                category="warning",
                data={"queue_task_uuid": str(queue_task_uuid)},
            )

        _check_ownership(request, obj)
        self._apply_retry_rate_limit(request, obj, queue_task_uuid)

        result = retry_failed_queue_task(str(queue_task_uuid))
        if result == "queued":
            response_url = request.build_absolute_uri(
                reverse("mo_queue_detail", args=[queue_task_uuid])
            )
            status_stream_url = request.build_absolute_uri(
                reverse("mo_queue_status_stream", args=[queue_task_uuid])
            )
            cancel_url = request.build_absolute_uri(
                reverse("mo_queue_cancel", args=[queue_task_uuid])
            )
            retry_url = request.build_absolute_uri(
                reverse("mo_queue_retry", args=[queue_task_uuid])
            )
            return mo_response_kit.json_response(
                code="QUEUED",
                category="success",
                data={
                    "queue_id": str(queue_task_uuid),
                    "response_url": response_url,
                    "status_stream_url": status_stream_url,
                    "cancel_url": cancel_url,
                    "retry_url": retry_url,
                },
            )

        return mo_response_kit.json_response(
            code="QUEUE_TASK_NOT_RETRIABLE",
            category="warning",
            data={
                "queue_id": str(queue_task_uuid),
                "job_status": obj.job_status,
            },
        )

    def _apply_retry_rate_limit(self, request, obj, queue_task_uuid):
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        if not _is_queue_mode_api(api):
            return
        limit = getattr(api, "queue_retry_api_limit", None)
        if not limit:
            return
        limited = is_ratelimited(
            request,
            group=f"{api.api_url_name}_{queue_task_uuid}_retry",
            key="user_or_ip",
            rate=limit,
            increment=True,
        )
        mo_validation_kit.ensure_falsey(
            limited,
            msg=rate_limit_exceeded_message,
            code="API_RATE_LIMITED",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_live_progress(queue_task_uuid: str) -> dict:
    """Fetch live progress from Redis, falling back to rehydration."""
    state = get_queue_status(queue_task_uuid)
    if _state_status(state) == "unknown":
        state = rehydrate_queue_state(queue_task_uuid)
    if _state_status(state) == "unknown":
        return {}
    raw = state.get("progress") or {}
    return {
        "percent": max(0, min(100, int(raw.get("percent", 0)))),
        "current_step": str(raw.get("current_step", "")),
        "current_message": str(raw.get("current_message", "")),
    }


def _check_ownership(request, obj: MOQueue) -> None:
    """Raise PERMISSION_DENIED if the request user does not own this task."""
    if not obj.user_ref_id:
        return
    request_user_id = getattr(request.user, "id", None)
    mo_validation_kit.ensure_equal(
        str(obj.user_ref_id),
        str(request_user_id),
        msg=access_denied_message,
        code="PERMISSION_DENIED",
    )


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


def _response_code_to_http_status(response_code: int | None, default: int = 200) -> int:
    try:
        status = int(str(response_code))
        if 100 <= status <= 599:
            return status
    except Exception:
        pass
    return int(default)


def _normalize_completed_response(response_json):
    """
    Normalize legacy wrapped queue payload:
    {"status_code": <int>, "data": <response_json>}
    """
    if (
        isinstance(response_json, dict)
        and "data" in response_json
        and "status_code" in response_json
    ):
        embedded_status = response_json.get("status_code")
        inner = response_json.get("data")
        if isinstance(embedded_status, int) and isinstance(inner, dict):
            return inner, embedded_status
    return response_json, None


def _is_queue_mode_api(api_instance) -> bool:
    return getattr(api_instance, "process_mode", "direct") == "queue"


def _status_payload(state: dict) -> dict:
    """
    Build the status dict used by SSE streaming.
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
        "started_at": state.get("started_at"),
        "updated_at": state.get("updated_at"),
    }


def _stream_state_payload(state: dict) -> dict:
    """Identical to ``_status_payload``; also used for SSE deduplication."""
    return _status_payload(state)


def _serialize_queue_obj(obj: MOQueue) -> dict:
    """Serialize all model columns and decode compressed JSON fields when present."""
    data = {}
    for field in obj._meta.concrete_fields:
        key = field.attname if getattr(field, "attname", None) else field.name
        value = getattr(obj, key)

        if isinstance(field, models.JSONField):
            value = _maybe_decode_compressed_json(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        elif key.endswith("_id") and value is not None:
            value = str(value)

        data[key] = value

    return data


def _maybe_decode_compressed_json(payload):
    if not isinstance(payload, dict):
        return payload
    if not (payload.get("__compressed__") and payload.get("codec") == "gzip+base64"):
        return payload
    raw_b64 = payload.get("data")
    if not raw_b64:
        return payload
    try:
        compressed = base64.b64decode(raw_b64)
        raw = gzip.decompress(compressed).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return payload


def _safe_int(raw, default: int, minimum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)
