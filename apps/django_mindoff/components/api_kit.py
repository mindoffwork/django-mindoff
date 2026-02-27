from rest_framework import status
from rest_framework.response import Response
from django.http import JsonResponse
from rest_framework.views import APIView
from types import SimpleNamespace
from functools import wraps
from .response_kit import mo_response_kit
from .validation_kit import mo_validation_kit, MindoffValidationError
from typing import Any, Dict
from ._helper_kit.validate_schema import validate_schema
import json
from typing import Any, Dict, List, Union, Optional, Literal, Callable
from django.urls import reverse
from ._api_kit.redis import update_progress
from ._api_kit.queue_process import enqueue_process
from django_ratelimit.core import is_ratelimited
from django.urls import get_resolver
from rest_framework.exceptions import (
    NotAuthenticated,
    AuthenticationFailed,
    PermissionDenied,
    Throttled,
)

# ----------------
# Constants
# ----------------
ALLOWED_METHODS = ["get", "post", "put", "delete"]
ALLOWED_PROCESS_MODES = ["direct", "queue"]


# ----------------
# Classes
# ----------------
class MindoffAPIMixin(APIView):
    # 1. API Identity
    api_url_name: str = ""
    api_name: str = ""
    api_description: str = ""

    # 2. Access Rules
    authentication_classes: list = []
    permission_classes: list = []
    method: Literal["get", "post", "put", "delete"] = ""

    # 3. Execution Rules
    process_mode: Literal["direct", "queue"] = "direct"
    allow_duplicate_queue: bool = False

    # 4. Request Rules
    payload_schema: list | dict | None = None
    max_payload_size: int | float | None = 10  # in Megabytes(MB)
    max_payload_depth: int | None = None
    payload_validation: Literal["strict", "basic", None] = None

    # 5. Usage Limits Per User
    api_request_limit: str | None = "30/m"
    queue_status_polling_limit: str | None = "30/m"
    queue_status_streaming_limit: int | None = 3

    def queue_progress(
        self,
        request,
        *,
        progress: int,
        step: str | None = None,
        message: str | None = None,
    ):
        queue_task_uuid = getattr(request, "queue_task_uuid", None)
        if not queue_task_uuid:
            return
        update_progress(
            queue_task_uuid,
            progress=progress,
            step=step,
            message=message,
        )

    def run(self, request, *args, **kwargs):
        raise NotImplementedError("You must implement run() in your API class")

    def get(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def _handle_request_logic(self, request, *args, **kwargs):
        if self.process_mode == "queue":
            mo_validation_kit.ensure_falsey(
                bool(request.FILES),
                msg="Asynchronous API does not support multipart or file uploads.",
            )
            queue_id = enqueue_process(
                request=request,
                api_instance=self,
                args=args,
                kwargs=kwargs,
            )
            mo_validation_kit.ensure_truthy(
                queue_id,
                msg="Failed to start the process. Please try again.",
            )

            status_polling_url = request.build_absolute_uri(
                reverse("mo_queue_status_polling", args=[queue_id])
            )
            status_streaming_url = request.build_absolute_uri(
                reverse("mo_queue_status_streaming", args=[queue_id])
            )
            response_data = {
                "queue_id": queue_id,
                "status_polling_url": status_polling_url,
                "status_streaming_url": status_streaming_url,
            }
            return mo_response_kit.json_response(
                code="QUEUED",
                category="success",
                data=response_data,
            )
        return self.run(request, *args, **kwargs)

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)
            return self._dispatch_ensure_response_is_rendered(response)

    def validate_api_configuration(self):

        # ---------- REQUIRED ATTRIBUTES ----------
        # required_attrs = [
        #     "api_url_name",
        #     "api_name",
        #     "api_description",
        #     "method",
        #     "process_mode",
        # ]
        for attr_name in ("authentication_classes", "permission_classes"):
            classes = getattr(self, attr_name)
            mo_validation_kit.ensure_type(
                classes,
                (list, tuple),
                msg=f"`{attr_name}` in {self.__class__.__name__} must be a list or tuple, not {type(classes).__name__}",
                is_exception=True,
                code="API_CONFIG_ERR",
            )
            for cls in classes:
                mo_validation_kit.ensure_truthy(
                    callable(cls),
                    msg=f"Invalid entry in `{attr_name}` for {self.__class__.__name__}: '{cls}'. Expected a class, got a string.",
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
        mo_validation_kit.ensure_truthy(
            self.method,
            msg=f"A Valid Method must be configured in `{self.api_url_name}` api",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_truthy(
            self.api_url_name,
            msg="`api_url_name` must not be empty or None",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_type(
            self.api_url_name,
            str,
            msg="`api_url_name` must be a string",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        resolver = get_resolver()
        if self.api_url_name in resolver.reverse_dict:
            matches = [self.api_url_name]
        else:
            matches = []
        mo_validation_kit.ensure_truthy(
            matches,
            msg=f"`api_url_name='{self.api_url_name}' not found in urls.py",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_equal(
            len(matches),
            1,
            msg=f"`api_url_name='{self.api_url_name}' occurs more than once in urls.py",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_truthy(
            self.api_name,
            msg="`api_name` must not be empty or None",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_type(
            self.api_name,
            str,
            msg="`api_name` must be a string",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_not_equal(
            self.api_description,
            None,
            msg="`api_description` must be a string and cannot be None",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_type(
            self.api_description,
            str,
            msg="`api_description` must be a string",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_type(
            self.allow_duplicate_queue,
            bool,
            msg="`allow_duplicate_queue` must be boolean",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_in(
            type(self.payload_schema),
            (list, dict, type(None)),
            msg="`payload_schema` must be list | dict | None",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        for attr in ("max_payload_size", "max_payload_depth"):
            value = getattr(self, attr)
            if value is not None:
                mo_validation_kit.ensure_type(
                    value,
                    (int, float),
                    msg=f"`{attr}` must be int | float | None",
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
        mo_validation_kit.ensure_in(
            self.payload_validation,
            ("strict", "basic", None),
            msg="`payload_validation` must be 'strict', 'basic' or None",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        for attr in ("api_request_limit", "queue_status_polling_limit"):
            value = getattr(self, attr)
            if value is not None:
                mo_validation_kit.ensure_type(
                    value,
                    str,
                    msg=f"`{attr}` must be str | None",
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
                mo_validation_kit.ensure_regex(
                    value,
                    r"^[1-9]\d*/[smhd]$",
                    msg=f"`{attr}` must match '<int>/(s|m|h|d)' format. Example: '10/m'.",
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
        if self.queue_status_streaming_limit is not None:
            mo_validation_kit.ensure_type(
                self.queue_status_streaming_limit,
                int,
                msg="`queue_status_streaming_limit` must be int | None",
                is_exception=True,
                code="API_CONFIG_ERR",
            )
            mo_validation_kit.ensure_greater_equal(
                self.queue_status_streaming_limit,
                0,
                msg="`queue_status_streaming_limit` must be greater than or equal to zero",
                is_exception=True,
                code="API_CONFIG_ERR",
            )
        mo_validation_kit.ensure_in(
            self.process_mode,
            ALLOWED_PROCESS_MODES,
            msg=f"`process_mode` must be one of {ALLOWED_PROCESS_MODES}",
            is_exception=True,
            code="API_CONFIG_ERR",
        )

    def _dispatch_ensure_response_is_rendered(self, response):
        if not hasattr(response, "accepted_renderer"):
            from rest_framework.renderers import JSONRenderer

            response.accepted_renderer = JSONRenderer()
            response.accepted_media_type = "application/json"
            response.renderer_context = self.get_renderer_context()
        return response

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._initial_validate_request_method(request)
        self._initial_validate_api_rate_limit(request)
        if request.method in ("POST", "PUT"):
            self._initial_validate_request_payload(request)

    def _initial_validate_request_method(self, request):
        mo_validation_kit.ensure_in(
            self.method.lower(),
            ALLOWED_METHODS,
            msg=f"`method` configured in API is not allowed. Allowed methods are {ALLOWED_METHODS}",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_equal(
            self.method.upper(),
            request.method,
            msg=f"Method '{request.method}' not allowed.",
            code="INVALID_METHOD",
        )

    def _initial_validate_api_rate_limit(self, request):
        if self.api_request_limit:
            limited = is_ratelimited(
                request._request,
                group=self.api_url_name,
                key="user_or_ip",
                rate=self.api_request_limit,
                increment=True,
            )
            mo_validation_kit.ensure_falsey(
                limited,
                msg="Maximum allowed request limit exceeded by the user for the API.",
                code="API_RATE_LIMITED",
            )

    def _initial_validate_request_payload(self, request):
        payload = request.data if request.data not in (None, "") else {}
        # --- 1. payload size check ---
        if self.max_payload_size is not None:
            mo_validation_kit.ensure_greater(
                self.max_payload_size,
                0,
                msg=(
                    f"`max_payload_size` must be configured with a positive integer (> 0) "
                    f"for the `{self.api_url_name}` API"
                ),
                is_exception=True,
                code="API_CONFIG_ERR",
            )
            content_length = request.META.get("CONTENT_LENGTH")
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)

                mo_validation_kit.ensure_lesser_equal(
                    round(float(size_mb), 2),
                    round(float(self.max_payload_size), 2),
                    msg=f"Payload too large: {size_mb:.2f} MB (Limit: {self.max_payload_size} MB)",
                    code="PAYLOAD_TOO_LARGE",
                )

        # --- 2. payload depth check ---
        if self.max_payload_depth is not None:
            mo_validation_kit.ensure_greater(
                self.max_payload_depth,
                0,
                msg=(
                    f"`max_payload_depth` must be configured with a positive integer (> 0) "
                    f"for the `{self.api_url_name}` API"
                ),
                is_exception=True,
                code="API_CONFIG_ERR",
            )

        # --- 3. payload schema check
        if self.payload_validation is not None:
            if self.payload_schema is None:
                mo_validation_kit.ensure_falsey(
                    payload,
                    msg="This API does not accept a request payload.",
                    code="PAYLOAD_NOT_ALLOWED",
                )
            else:
                validate_schema(
                    payload,
                    self.payload_schema,
                    max_nesting_depth=self.max_payload_depth,
                    validation_mode=self.payload_validation,
                )

    def handle_exception(self, exc):
        # 1. Authentication related exceptions
        if isinstance(exc, NotAuthenticated):
            return mo_response_kit.json_response(
                code="NOT_AUTHENTICATED",
                category="danger",
            )
        if isinstance(exc, AuthenticationFailed):
            return mo_response_kit.json_response(
                code="AUTHENTICATION_FAILED",
                category="danger",
            )

        if isinstance(exc, PermissionDenied):
            return mo_response_kit.json_response(
                code="PERMISSION_DENIED",
                category="danger",
            )

        if isinstance(exc, Throttled):
            return mo_response_kit.json_response(
                code="RATE_LIMITED",
                category="warning",
            )

        # 2. Validation related exceptions
        code = getattr(exc, "code", None) or "UNEXPECTED_ERR"
        category = getattr(exc, "category", None) or "danger"
        data = getattr(exc, "data", None) or []
        if isinstance(exc, MindoffValidationError):
            return mo_response_kit.json_response(
                code=code,
                category=category,
                data=data,
            )
        return mo_response_kit.json_response(
            code=code,
            category=category,
            data=data,
            exception=exc,
        )


# ----------------
# Functions
# ----------------
def api_guardian(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        try:
            return func(request, *args, **kwargs)
        except MindoffValidationError as exc:
            return mo_response_kit.json_response(
                code=exc.code,
                category=exc.category,
                **exc.data,
            )
        except Exception as exc:
            if isinstance(exc, MindoffValidationError):
                return mo_response_kit.json_response(
                    code=exc.code,
                    category=exc.category,
                    data=exc.data,
                )
            if isinstance(exc, NotAuthenticated):
                return mo_response_kit.json_response(
                    code="NOT_AUTHENTICATED",
                    category="danger",
                )
            if isinstance(exc, AuthenticationFailed):
                return mo_response_kit.json_response(
                    code="AUTHENTICATION_FAILED",
                    category="danger",
                )

            if isinstance(exc, PermissionDenied):
                return mo_response_kit.json_response(
                    code="PERMISSION_DENIED",
                    category="danger",
                )

            if isinstance(exc, Throttled):
                return mo_response_kit.json_response(
                    code="RATE_LIMITED",
                    category="warning",
                )
            return mo_response_kit.json_response(
                code="UNEXPECTED_ERR",
                category="danger",
                data=[],
                exception=exc,
            )

    return wrapper


# ----------------
# Entry Point
# ----------------
mo_api_kit = SimpleNamespace(
    api_guardian=api_guardian,
    MindoffAPIMixin=MindoffAPIMixin,
)
