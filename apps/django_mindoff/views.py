# views.py
import argparse
import importlib
import json
import time

from .components import managers
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.shortcuts import get_object_or_404
from django.urls import reverse

from .models import MOQueue
from .components._api_kit.redis import get_queue_status
from .components._api_kit.queue_process import (
    rehydrate_queue_state,
    cancel_queue_task,
    retry_failed_queue_task,
)
from django_ratelimit.core import is_ratelimited
from .components.validation_kit import mo_validation_kit
from .components.helper_kit import get_api_class_from_url_name
from .components._api_kit.redis import sse_event, acquire_sse_slot, release_sse_slot
from .components.api_kit import MindoffAPIMixin
from typing import Any, Dict, List, Union, Optional, Literal, Callable
from .components.response_kit import mo_response_kit

access_denied_message = "User not allowed to access this queue task"


class MindoffQueuePollingView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_status_polling"
    api_name: str = "Mindoff Queue Status Polling"
    api_description: str = "Get the status of a specific queue task"
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
    process_mode: Literal["direct", "queue"] = "direct"
    api_request_limit: str | None = "120/m"

    def run(self, request, queue_task_uuid):

        # 1. Load task from SQL (ownership + existence)
        try:
            obj = MOQueue.objects.get(id=queue_task_uuid)
        except MOQueue.DoesNotExist:
            return mo_response_kit.json_response(
                code="QUEUE_TASK_NOT_FOUND",
                category="warning",
                data={"queue_task_uuid": queue_task_uuid},
            )

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
                return mo_response_kit.json_response(
                    code="PERMISSION_DENIED", category="danger"
                )

        # 3. Try Redis first (live state)
        redis_state = get_queue_status(queue_task_uuid)
        if redis_state.get("status") == "unknown":
            redis_state = rehydrate_queue_state(str(queue_task_uuid))
        if redis_state.get("status") != "unknown":
            data = {
                "queue_id": str(queue_task_uuid),
                **redis_state,
            }
            return mo_response_kit.json_response(
                code="SUCCESS", category="success", data=data
            )

        # 4. Redis expired → fallback to SQL
        data = {
            "queue_id": str(queue_task_uuid),
            "status": obj.status,
            "progress": 100 if obj.status == "completed" else 0,
            "current_step": "",
            "message": obj.error or "",
            "last_updated_at": obj.updated_at.isoformat(),
        }
        return mo_response_kit.json_response(
            code="SUCCESS", category="success", data=data
        )


class MindoffQueueStreamingView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_status_streaming"
    api_name: str = "Mindoff Queue Status Streaming"
    api_description: str = (
        "Get the status of a specific queue task in real-time using Server-Sent Events"
    )
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = "get"
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
            msg=access_denied_message,
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
                    if self.__is_unknown_state(state):
                        state = rehydrate_queue_state(str(queue_task_uuid))
                        if self.__is_unknown_state(state):
                            yield sse_event(self.__fallback_state(obj))
                            return
                    event = self.__maybe_sse_event(state, last_state)
                    if event is not None:
                        yield event
                        last_state = state
                    if self.__is_terminal_state(state):
                        return
                    time.sleep(1)
            finally:
                release_sse_slot(obj.owner_id)

        return generator()

    def __is_unknown_state(self, state):
        return state.get("status") == "unknown"

    def __is_terminal_state(self, state):
        return state.get("status") in ("completed", "failed", "cancelled")

    def __fallback_state(self, obj):
        return {
            "status": obj.status,
            "progress": 100 if obj.status == "completed" else 0,
            "message": obj.error or "",
        }

    def __maybe_sse_event(self, state, last_state):
        if state == last_state:
            return None
        return sse_event(state)


class MindoffQueueCancelView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_cancel"
    api_name: str = "Mindoff Queue Cancel"
    api_description: str = "Cancel a running queue task"
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

        status = cancel_queue_task(str(queue_task_uuid))
        if status == "cancelled":
            return mo_response_kit.json_response(
                code="SUCCESS",
                category="success",
                data={"queue_id": str(queue_task_uuid), "status": "cancelled"},
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


class MindoffQueueRetryView(MindoffAPIMixin):
    api_url_name: str = "mo_queue_retry"
    api_name: str = "Mindoff Queue Retry"
    api_description: str = "API Description"
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

        status = retry_failed_queue_task(str(queue_task_uuid))
        if status == "queued":
            status_polling_url = request.build_absolute_uri(
                reverse("mo_queue_status_polling", args=[queue_task_uuid])
            )
            status_streaming_url = request.build_absolute_uri(
                reverse("mo_queue_status_streaming", args=[queue_task_uuid])
            )
            return mo_response_kit.json_response(
                code="QUEUED",
                category="success",
                data={
                    "queue_id": str(queue_task_uuid),
                    "status_polling_url": status_polling_url,
                    "status_streaming_url": status_streaming_url,
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


# ---------- Helper Functions ----------
