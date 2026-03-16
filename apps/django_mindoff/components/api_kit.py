import threading
from functools import wraps
from types import SimpleNamespace
from typing import Literal
from django.urls import get_resolver
from django.urls import reverse
from django_ratelimit.core import is_ratelimited
from redis.exceptions import RedisError
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from ._api_kit.api_router import APIVersionRouter
from ._api_kit.queue_process import enqueue_process
from ._api_kit.redis import get_queue_status, mark_cancelled, update_progress
from ._helper_kit.validate_schema import validate_schema
from .response_kit import mo_response_kit
from .validation_kit import MindoffValidationError, mo_validation_kit

# ----------------
# Constants
# ----------------
ALLOWED_METHODS = ["get", "post", "put", "delete"]
ALLOWED_PROCESS_MODES = ["direct", "queue"]
_PROGRESS_STEP_REQUIRED_KEYS = {"label", "percent"}
_test_force_direct = threading.local()


# ----------------
# Classes
# ----------------
class MindoffAPIMixin(APIView):
    """
    Mixin class used to define a Mindoff API.

    Mindoff APIs are built on top of Django Rest Framework's APIView.
    This class provides common functionality for all Mindoff APIs.

    Usage Example::
        from django_mindoff.components.api_kit import MindoffAPIMixin

        class MyAPI(MindoffAPIMixin):
            api_url_name = "my_api"
            api_name = "My API"
            api_description = "This is an example API."
            method = "get"

            def run(self, request):
                return Response({"message": "Hello World!"})
    """

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
    progress_steps: dict | None = None

    # 4. Request Rules
    payload_schema: list | dict | None = None
    max_payload_size: int | float | None = 10  # megabytes
    max_payload_depth: int | None = None
    payload_validation: Literal["strict", "basic", None] = None

    # 5. Usage Limits Per User
    api_request_limit: str | None = "30/m"
    queue_detail_api_limit: str | None = "30/m"
    queue_cancel_api_limit: str | None = "30/m"
    queue_retry_api_limit: str | None = "30/m"
    queue_status_stream_api_limit: int | None = 3

    def _get_progress_steps_config(self) -> dict:
        """Return class-level progress_steps config and block runtime instance overrides."""
        mo_validation_kit.ensure_falsey(
            "progress_steps" in self.__dict__,
            msg=(
                "`progress_steps` must be defined as a class-level API config and "
                "must not be reassigned on `self` during request execution."
            ),
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        steps = getattr(self.__class__, "progress_steps", None)
        return steps if isinstance(steps, dict) else {}

    def dispatch(self, request, *args, **kwargs):
        """Add one line docstring here"""
        try:
            return super().dispatch(request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)
            return self._dispatch_ensure_response_is_rendered(response)

    def initial(self, request, *args, **kwargs):
        """Add one line docstring here"""
        super().initial(request, *args, **kwargs)
        self._initial_validate_request_method(request)
        self._initial_validate_api_rate_limit(request)
        if request.method in ("POST", "PUT"):
            self._initial_validate_request_payload(request)

    def validate_api_configuration(self):
        """Add one line docstring here"""
        required_attrs = ("api_url_name", "api_name", "api_description", "method")
        for attr in required_attrs:
            mo_validation_kit.ensure_truthy(
                hasattr(self, attr),
                msg=f"`{attr}` must be defined on the API class.",
                is_exception=True,
                code="API_CONFIG_ERR",
            )

        for attr_name in ("authentication_classes", "permission_classes"):
            if not hasattr(self, attr_name):
                continue
            classes = getattr(self, attr_name)
            mo_validation_kit.ensure_type(
                classes,
                (list, tuple),
                msg=(
                    f"`{attr_name}` in {self.__class__.__name__} must be a list or tuple, "
                    f"not {type(classes).__name__}"
                ),
                is_exception=True,
                code="API_CONFIG_ERR",
            )
            for cls in classes:
                mo_validation_kit.ensure_truthy(
                    callable(cls),
                    msg=(
                        f"Invalid entry in `{attr_name}` for {self.__class__.__name__}: "
                        f"'{cls}'. Expected a class, got a string."
                    ),
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
        mo_validation_kit.ensure_truthy(
            self.method,
            msg=f"A valid method must be configured in `{self.api_url_name}` API",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_in(
            self.method.lower(),
            ALLOWED_METHODS,
            msg=(
                f"`method` configured in API is not allowed. "
                f"Allowed methods are {ALLOWED_METHODS}"
            ),
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
        matches = (
            [self.api_url_name] if self.api_url_name in resolver.reverse_dict else []
        )
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
        if hasattr(self, "allow_duplicate_queue"):
            mo_validation_kit.ensure_type(
                self.allow_duplicate_queue,
                bool,
                msg="`allow_duplicate_queue` must be boolean",
                is_exception=True,
                code="API_CONFIG_ERR",
            )
        if hasattr(self, "payload_schema"):
            mo_validation_kit.ensure_in(
                type(self.payload_schema),
                (list, dict, type(None)),
                msg="`payload_schema` must be list | dict | None",
                is_exception=True,
                code="API_CONFIG_ERR",
            )
        for attr in ("max_payload_size", "max_payload_depth"):
            if not hasattr(self, attr):
                continue
            value = getattr(self, attr)
            if value is not None:
                mo_validation_kit.ensure_type(
                    value,
                    (int, float),
                    msg=f"`{attr}` must be int | float | None",
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
                mo_validation_kit.ensure_greater(
                    value,
                    0,
                    msg=(
                        f"`{attr}` must be configured with a positive integer (> 0) "
                        f"for the `{self.api_url_name}` API"
                    ),
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
        if hasattr(self, "payload_validation"):
            mo_validation_kit.ensure_in(
                self.payload_validation,
                ("strict", "basic", None),
                msg="`payload_validation` must be 'strict', 'basic' or None",
                is_exception=True,
                code="API_CONFIG_ERR",
            )
        for attr in (
            "api_request_limit",
            "queue_detail_api_limit",
            "queue_cancel_api_limit",
            "queue_retry_api_limit",
        ):
            if not hasattr(self, attr):
                continue
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
        if hasattr(self, "queue_status_stream_api_limit"):
            if self.queue_status_stream_api_limit is not None:
                mo_validation_kit.ensure_type(
                    self.queue_status_stream_api_limit,
                    int,
                    msg="`queue_status_stream_api_limit` must be int | None",
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
                mo_validation_kit.ensure_greater_equal(
                    self.queue_status_stream_api_limit,
                    0,
                    msg="`queue_status_stream_api_limit` must be >= 0",
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
        if hasattr(self, "process_mode"):
            mo_validation_kit.ensure_in(
                self.process_mode,
                ALLOWED_PROCESS_MODES,
                msg=f"`process_mode` must be one of {ALLOWED_PROCESS_MODES}",
                is_exception=True,
                code="API_CONFIG_ERR",
            )
        if hasattr(self, "progress_steps"):
            steps_cfg = self._get_progress_steps_config()
            if steps_cfg:
                mo_validation_kit.ensure_equal(
                    self.process_mode,
                    "queue",
                    msg=(
                        f"`progress_steps` is defined on `{self.api_url_name}` but "
                        f"`process_mode` is '{self.process_mode}'. "
                        f"`progress_steps` is only valid for queue-mode APIs."
                    ),
                    is_exception=True,
                    code="API_CONFIG_ERR",
                )
                _validate_progress_steps(steps_cfg, self.api_url_name)

    def get(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self._handle_request_logic(request, *args, **kwargs)

    def run(self, request, *args, **kwargs):
        """Define API business logic in subclasses."""
        raise NotImplementedError("You must implement run() in your API class")

    def progress_checkpoint(
        self,
        request,
        checkpoint_key: str,
        *,
        msg: str | None = None,
    ):
        """
        Record progress for a queue task at a checkpoint, updating the percent
        and label while validating the checkpoint key and checking for cancellation.

        Usage:

        ```python
        class CreateOrderAPI(MindoffAPIMixin):
            process_mode = "queue"
            progress_steps = {
                "validate": {"label": "Validated input", "percent": 10},
                "process": {"label": "Processed data", "percent": 60},
                "finalize": {"label": "Finalized response", "percent": 90},
            }

            def run(self, request, *args, **kwargs):
                self.progress_checkpoint(request, "validate")
                self.progress_checkpoint(request, "process", msg="Rows processed")
                self.progress_checkpoint(request, "finalize")
                return mo_response_kit.json_response(
                    code="SUCCESS",
                    category="success",
                )
        ```

        Parameters:

        - `request` (`Any`): Current request object. Queue context is read from
          `request.queue_task_uuid`.
        - `checkpoint_key` (`str`): Key in class-level `progress_steps` mapped to a
          step config containing `label` and `percent`.
        - `msg` (`str | None`, default=`None`): Optional progress message.
          When omitted, configured step `label` is used.

        Possible responses:

        - Returns `None` when request is not running in queue context.
        - Updates queue progress when checkpoint key is valid.
        - Raises `MindoffValidationError` when queue cancellation is requested.
        - Raises validation error when checkpoint key is not configured.

        Notes:

        - Intended for `process_mode="queue"` APIs.
        - `progress_steps` should follow:
          `{"step_key": {"label": "...", "percent": int}}`.
        - Use it at meaningful checkpoints so clients can show progress between queued and completed.
        """
        queue_task_uuid = getattr(request, "queue_task_uuid", None)
        if not queue_task_uuid:
            return
        steps = self._get_progress_steps_config()
        mo_validation_kit.ensure_truthy(
            checkpoint_key in steps,
            msg=(
                f"progress_checkpoint called with unknown key '{checkpoint_key}'. "
                f"Available keys: {list(steps.keys())}"
            ),
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        step_cfg = steps[checkpoint_key]
        percent = step_cfg["percent"]
        label = step_cfg["label"]
        message = msg if msg is not None else label
        redis_state = get_queue_status(str(queue_task_uuid))
        if _is_cancel_requested(redis_state):
            mark_cancelled(str(queue_task_uuid))
            raise MindoffValidationError(
                message="Queue task was cancelled",
                code="QUEUE_TASK_CANCELLED",
                category="warning",
                data={"queue_id": str(queue_task_uuid), "status": "cancelled"},
            )
        update_progress(
            queue_task_uuid,
            progress=percent,
            step=label,
            message=message,
        )

    def handle_exception(self, exc):
        """Add one line docstring here"""
        return _resolve_api_exception(exc)

    def _dispatch_ensure_response_is_rendered(self, response):
        if not hasattr(response, "accepted_renderer"):
            from rest_framework.renderers import JSONRenderer

            response.accepted_renderer = JSONRenderer()
            response.accepted_media_type = "application/json"
            response.renderer_context = self.get_renderer_context()
        return response

    def _initial_validate_request_method(self, request):
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
        if self.max_payload_size is not None:
            content_length = request.META.get("CONTENT_LENGTH")
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                mo_validation_kit.ensure_lesser_equal(
                    round(float(size_mb), 2),
                    round(float(self.max_payload_size), 2),
                    msg=f"Payload too large: {size_mb:.2f} MB (Limit: {self.max_payload_size} MB)",
                    code="PAYLOAD_TOO_LARGE",
                )
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

    def _handle_request_logic(self, request, *args, **kwargs):
        effective_mode = (
            "direct"
            if getattr(_test_force_direct, "active", False)
            else self.process_mode
        )
        if effective_mode == "queue":
            mo_validation_kit.ensure_falsey(
                bool(request.FILES),
                msg="Asynchronous API does not support multipart or file uploads.",
            )
            try:
                queue_id = enqueue_process(
                    request=request,
                    api_instance=self,
                    args=args,
                    kwargs=kwargs,
                )
            except Exception as exc:
                if _is_queue_service_unavailable_error(exc):
                    raise MindoffValidationError(
                        message=(
                            "Queuing service is currently unavailable. Please try again later."
                        ),
                        code="QUEUE_SERVICE_UNAVAILABLE",
                        category="danger",
                        data={"process_mode": "queue"},
                    )
                raise
            mo_validation_kit.ensure_truthy(
                queue_id,
                msg="Failed to start the process. Please try again.",
            )
            response_url = request.build_absolute_uri(
                reverse("mo_queue_detail", args=[queue_id])
            )
            status_stream_url = request.build_absolute_uri(
                reverse("mo_queue_status_stream", args=[queue_id])
            )
            cancel_url = request.build_absolute_uri(
                reverse("mo_queue_cancel", args=[queue_id])
            )
            retry_url = request.build_absolute_uri(
                reverse("mo_queue_retry", args=[queue_id])
            )
            return mo_response_kit.json_response(
                code="QUEUED",
                category="success",
                data={
                    "queue_id": queue_id,
                    "response_url": response_url,
                    "status_stream_url": status_stream_url,
                    "cancel_url": cancel_url,
                    "retry_url": retry_url,
                    "progress_steps": (self._get_progress_steps_config()) or {},
                },
            )

        return self.run(request, *args, **kwargs)


# ----------------
# Functions
# ----------------
def api_guardian(func):
    """Add Full Scale Docstring here"""

    @wraps(func)
    def wrapper(request, *args, **kwargs):
        try:
            return func(request, *args, **kwargs)
        except Exception as exc:
            return _resolve_api_exception(exc)

    return wrapper


# ----------------
# Helper Functions
# ----------------
def _is_queue_service_unavailable_error(exc: Exception) -> bool:
    if isinstance(exc, (RedisError, ConnectionError, TimeoutError)):
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "connection refused",
            "cannot connect",
            "connection error",
            "connection reset",
        )
    )


def _is_cancel_requested(redis_state: dict) -> bool:
    return redis_state.get("job_status") == "cancelled" or bool(
        redis_state.get("is_cancel")
    )


def _validate_progress_steps(steps: dict, api_url_name: str):
    if not steps:
        return
    mo_validation_kit.ensure_type(
        steps,
        dict,
        msg=f"`progress_steps` in `{api_url_name}` must be a dict",
        is_exception=True,
        code="API_CONFIG_ERR",
    )
    seen_percents = []
    for key, value in steps.items():
        mo_validation_kit.ensure_type(
            key,
            str,
            msg=f"`progress_steps` key `{key!r}` in `{api_url_name}` must be a string",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_truthy(
            key.strip(),
            msg=f"`progress_steps` key in `{api_url_name}` must not be empty",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_type(
            value,
            dict,
            msg=f"`progress_steps['{key}']` in `{api_url_name}` must be a dict",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        missing = _PROGRESS_STEP_REQUIRED_KEYS - set(value.keys())
        mo_validation_kit.ensure_falsey(
            missing,
            msg=(
                f"`progress_steps['{key}']` in `{api_url_name}` is missing "
                f"required keys: {missing}"
            ),
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_type(
            value["label"],
            str,
            msg=f"`progress_steps['{key}']['label']` in `{api_url_name}` must be a string",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_truthy(
            value["label"].strip(),
            msg=f"`progress_steps['{key}']['label']` in `{api_url_name}` must not be empty",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_type(
            value["percent"],
            int,
            msg=f"`progress_steps['{key}']['percent']` in `{api_url_name}` must be an int",
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_greater_equal(
            value["percent"],
            1,
            msg=(
                f"`progress_steps['{key}']['percent']` in `{api_url_name}` "
                f"must be between 1 and 99 (0 = queued, 100 = completed)"
            ),
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        mo_validation_kit.ensure_lesser_equal(
            value["percent"],
            99,
            msg=(
                f"`progress_steps['{key}']['percent']` in `{api_url_name}` "
                f"must be between 1 and 99 (0 = queued, 100 = completed)"
            ),
            is_exception=True,
            code="API_CONFIG_ERR",
        )
        seen_percents.append((key, value["percent"]))
    for i in range(1, len(seen_percents)):
        prev_key, prev_pct = seen_percents[i - 1]
        curr_key, curr_pct = seen_percents[i]
        mo_validation_kit.ensure_greater(
            curr_pct,
            prev_pct,
            msg=(
                f"`progress_steps` in `{api_url_name}`: percent for `{curr_key}` "
                f"({curr_pct}) must be greater than `{prev_key}` ({prev_pct}). "
                f"Steps must have strictly increasing percent values."
            ),
            is_exception=True,
            code="API_CONFIG_ERR",
        )


def _resolve_api_exception(exc: Exception):
    if isinstance(exc, NotAuthenticated):
        return mo_response_kit.json_response(
            code="NOT_AUTHENTICATED", category="danger"
        )
    if isinstance(exc, AuthenticationFailed):
        return mo_response_kit.json_response(
            code="AUTHENTICATION_FAILED", category="danger"
        )
    if isinstance(exc, PermissionDenied):
        return mo_response_kit.json_response(
            code="PERMISSION_DENIED", category="danger"
        )
    if isinstance(exc, Throttled):
        return mo_response_kit.json_response(code="RATE_LIMITED", category="warning")
    code = getattr(exc, "code", None) or "UNEXPECTED_ERR"
    category = getattr(exc, "category", None) or "danger"
    data = getattr(exc, "data", None) or []
    if isinstance(exc, MindoffValidationError):
        return mo_response_kit.json_response(code=code, category=category, data=data)
    return mo_response_kit.json_response(
        code=code,
        category=category,
        data=data,
        exception=exc,
    )


# ----------------
# Entry Point
# ----------------
mo_api_kit = SimpleNamespace(
    api_guardian=api_guardian,
    MindoffAPIMixin=MindoffAPIMixin,
    APIVersionRouter=APIVersionRouter,
)
