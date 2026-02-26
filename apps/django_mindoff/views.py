# views.py
import argparse
import importlib
import json
import time

from .components import managers
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.shortcuts import get_object_or_404

from .models import MOQueue
from .components._api_kit.redis import get_queue_status
from django_ratelimit.core import is_ratelimited
from .components.validation_kit import mo_validation_kit
from .components.helper_kit import get_api_class_from_url_name
from .components._api_kit.redis import sse_event, acquire_sse_slot, release_sse_slot
from .components.api_kit import MindoffAPIMixin
from typing import Any, Dict, List, Union, Optional, Literal, Callable


class MindoffQueuePollingView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_status_polling"
    api_name: str = "Mindoff Queue Status Polling"
    api_description: str = "API Description"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = "120/m"

    def run(self, request, queue_task_uuid):

        # 1. Load task from SQL (ownership + existence)
        obj = get_object_or_404(MOQueue, id=queue_task_uuid)

        # 2. Rate Limit API
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        if api.queue_status_polling_limit:
            limited = is_ratelimited(
                request,
                group=str(api.api_url_name) + "_" + str(queue_task_uuid),
                key="user_or_ip",
                rate=api.queue_status_polling_limit,
                increment=True,
            )
            mo_validation_kit.ensure_falsey(
                limited,
                msg="API Rate limit exceeded. Please try again after sometime.",
            )

        # 2. Permission check (same as SSE)
        if obj.user_ref_id:
            request_user_id = getattr(request.user, "id", None)
            if str(obj.user_ref_id) != str(request_user_id):
                return JsonResponse(
                    {"detail": "Not allowed to access this queue task"},
                    status=403,
                )

        # 3. Try Redis first (live state)
        redis_state = get_queue_status(queue_task_uuid)
        if redis_state.get("status") != "unknown":
            return JsonResponse(
                {
                    "queue_id": str(queue_task_uuid),
                    **redis_state,
                }
            )

        # 4. Redis expired → fallback to SQL
        return JsonResponse(
            {
                "queue_id": str(queue_task_uuid),
                "status": obj.status,
                "progress": 100 if obj.status == "completed" else 0,
                "current_step": "",
                "message": obj.error or "",
                "last_updated_at": obj.updated_at.isoformat(),
            }
        )


class MindoffQueueStreamingView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_status_streaming"
    api_name: str = "Mindoff Queue Status Streaming"
    api_description: str = "API Description"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = "120/m"

    def run(self, request, queue_task_uuid):
        obj = get_object_or_404(MOQueue, id=queue_task_uuid)

        # validation + rate limit
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
            msg="User not allowed to access this queue task",
            code="PERMISSION_DENIED",
        )

    def _acquire_sse_limit(self, obj):
        api = get_api_class_from_url_name(api_url_name=obj.api_url_name)()
        max_streams = api.queue_status_streaming_limit

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
                    if self._is_unknown_state(state):
                        yield sse_event(self._fallback_state(obj))
                        return
                    event = self._maybe_sse_event(state, last_state)
                    if event is not None:
                        yield event
                        last_state = state
                    if self._is_terminal_state(state):
                        return
                    time.sleep(1)
            finally:
                release_sse_slot(obj.owner_id)

        return generator()

    def _is_unknown_state(self, state):
        return state.get("status") == "unknown"

    def _is_terminal_state(self, state):
        return state.get("status") in ("completed", "failed")

    def _fallback_state(self, obj):
        return {
            "status": obj.status,
            "progress": 100 if obj.status == "completed" else 0,
            "message": obj.error or "",
        }

    def _maybe_sse_event(self, state, last_state):
        if state == last_state:
            return None
        return sse_event(state)


# ---------- Helper Functions ----------
