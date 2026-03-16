import re
import logging
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.checks import run_checks
from django.urls import clear_url_caches, reverse
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
)
from rest_framework.response import Response
from ....components.api_kit import (
    MindoffAPIMixin,
    _is_queue_service_unavailable_error,
    api_guardian,
)
from ....components.tdd_kit import MindoffTestCase
from ....components.response_kit import mo_response_kit
from ....components.validation_kit import MindoffValidationError

ALLOWED_METHODS = ["get", "post", "put", "delete"]
ALLOWED_PROCESS_MODES = ["direct", "queue"]
ALLOWED_RESPONSE_TYPES = ["json", "plain", "html", "xml", "binary", "others"]
VALIDATION_MODES = ["strict", "basic", None]
_CANONICAL_API = "canonical_api"
_CANONICAL_CLASS = "CanonicalApiV1APIView"
_CANONICAL_ROUTER_CLASS = "CanonicalApiRouter"
_CANONICAL_ROUTER_INST = "canonical_api_router"
_CANONICAL_HUMAN = "Canonical Api"
_CANONICAL_APP = "canonical_bootstrap_app"
User = get_user_model()
pass_code = "pass123"


@pytest.mark.django_db(transaction=True)
class TestAPIConfigurationValidation(MindoffTestCase):

    EXPECTED_CHECK_ID = "django_mindoff.API_CONFIG_ERR"

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    @pytest.mark.parametrize(
        ("attribute", "value", "error_message"),
        [
            ("api_url_name", "", "`api_url_name` must not be empty"),
            ("api_name", "", "`api_name` must not be empty"),
        ],
    )
    def test_config_empty_required_attributes(self, attribute, value, error_message):
        """BOUNDARY: Validates config empty required attributes."""
        api_name = f"test_empty_{attribute}_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, attribute, value, base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(error_message)

    def test_config_invalid_api_url_name_not_in_urls(self):
        """REJECTION: Validates config invalid api url name not in urls."""
        api_name = "test_invalid_url_name_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app,
            api_name,
            "api_url_name",
            "non_existent_url_name",
            base_path=self._dir,
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("not found in urls.py")

    def test_config_none_api_description(self):
        """BOUNDARY: Validates config none api description."""
        api_name = "test_none_desc_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "api_description", None, base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(
            "`api_description` must be a string and cannot be None"
        )

    def test_config_non_boolean_allow_duplicate_queue(self):
        """REJECTION: Validates config non boolean allow duplicate queue."""
        api_name = "test_invalid_dup_queue_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "allow_duplicate_queue", "true", base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("`allow_duplicate_queue` must be boolean")

    def test_config_skips_missing_optional_attributes(self, monkeypatch):
        """BOUNDARY: Validates config skips missing optional attributes."""
        api_name = "test_missing_optional_attrs_api"
        self._make_api(api_name)
        for attr in (
            "allow_duplicate_queue",
            "payload_schema",
            "payload_validation",
            "api_request_limit",
        ):
            monkeypatch.delattr(MindoffAPIMixin, attr, raising=False)
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        errors = run_checks()
        matching = [
            e
            for e in errors
            if e.id == self.EXPECTED_CHECK_ID and api_name in e.msg
        ]
        assert not matching, (
            f"Unexpected config errors: {[(e.id, e.msg) for e in errors]}"
        )

    @pytest.mark.parametrize(
        ("attribute", "value", "error_message"),
        [
            (
                "payload_schema",
                "invalid_string_schema",
                "`payload_schema` must be list | dict | None",
            ),
            (
                "max_payload_size",
                "not_a_number",
                "`max_payload_size` must be int | float | None",
            ),
        ],
    )
    def test_config_invalid_type_attributes(self, attribute, value, error_message):
        """REJECTION: Validates config invalid type attributes."""
        api_name = f"test_invalid_{attribute}_type_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, attribute, value, base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(error_message)

    def test_config_invalid_payload_validation_type(self):
        """REJECTION: Validates config invalid payload validation type."""
        api_name = "test_invalid_validation_type_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app,
            api_name,
            "payload_validation",
            "invalid_mode",
            base_path=self._dir,
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error(
            "`payload_validation` must be 'strict', 'basic' or None"
        )

    def test_config_invalid_rate_limit_format(self):
        """REJECTION: Validates config invalid rate limit format."""
        api_name = "test_invalid_rate_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "api_request_limit", "abc/m", base_path=self._dir
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("must match '<int>/(s|m|h|d)' format")

    def test_config_invalid_auth_entry_type(self):
        """REJECTION: Validates config invalid auth entry type."""
        api_name = "test_invalid_auth_entry_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app,
            api_name,
            "authentication_classes",
            '["BasicAuthentication"]',
            base_path=self._dir,
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("Expected a class, got a string")

    def test_config_invalid_queue_status_stream_limit(self):
        """REJECTION: Validates config invalid queue status stream limit."""
        api_name = "test_invalid_stream_limit_api"
        self._make_api(api_name)
        _modify_api_attribute(
            self._app,
            api_name,
            "queue_status_stream_api_limit",
            "3",
            base_path=self._dir,
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        self._assert_config_error("`queue_status_stream_api_limit` must be int | None")

    def test_config_progress_steps_valid_for_queue_mode(self):
        """ACCEPTANCE: Validates config progress steps valid for queue mode."""
        api_name = "test_valid_progress_steps_config_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "process_mode": "queue",
                "progress_steps": {
                    "validate": {"label": "Validating", "percent": 10},
                    "fetch": {"label": "Fetching", "percent": 40},
                },
            },
            base_path=self._dir,
        )
        clear_url_caches()
        _reload_api_modules(self._app, api_name)
        errors = run_checks()
        matching = [
            e
            for e in errors
            if e.id == self.EXPECTED_CHECK_ID
            and (api_url_name in e.msg or api_name in e.msg)
        ]
        assert (
            not matching
        ), f"Unexpected config errors: {[(e.id, e.msg) for e in errors]}"

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)

    def _assert_config_error(self, expected_msg_fragment: str):
        errors = run_checks()
        matching = [
            e
            for e in errors
            if e.id == self.EXPECTED_CHECK_ID and expected_msg_fragment in e.msg
        ]
        assert matching, (
            f"Expected config error with id={self.EXPECTED_CHECK_ID} "
            f"and message containing '{expected_msg_fragment}'.\n"
            f"Actual errors: {[(e.id, e.msg) for e in errors]}"
        )


@pytest.mark.django_db(transaction=True)
class TestAPIMixinAcceptance(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates
        self.user = User.objects.create_user(username="testuser", password=pass_code)

    def test_api_success_with_generated_api(self):
        """ACCEPTANCE: Validates api success with generated api."""
        api_url_name = self._make_api("test_basic_api")
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize(
        "response_type", ["json", "plain", "html", "xml", "binary"]
    )
    def test_api_success_with_response_types(self, response_type):
        """ACCEPTANCE: Validates api success with response types."""
        api_name = f"test_response_type_{response_type}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "response_type", response_type, base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize("http_method", ["get", "post", "put", "delete"])
    def test_api_success_with_allowed_methods(self, http_method):
        """ACCEPTANCE: Validates api success with allowed methods."""
        api_name = f"test_method_{http_method}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "method", http_method, base_path=self._dir
        )
        response = getattr(self.client, http_method)(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize("validation_mode", ["strict", "basic", None])
    def test_api_success_with_payload_validation_modes(self, validation_mode):
        """ACCEPTANCE: Validates api success with payload validation modes."""
        safe_name = str(validation_mode).lower() if validation_mode else "none"
        api_name = f"test_validation_mode_{safe_name}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"name": str, "age": int}',
                "payload_validation": validation_mode,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"name": "John", "age": 30}, format="json"
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_api_success_authenticated_user_with_auth_enabled(self):
        """ACCEPTANCE: Validates api success authenticated user with auth enabled."""
        api_name = "test_auth_enabled_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "authentication_classes": "[BasicAuthentication]",
                "permission_classes": "[IsAuthenticated]",
            },
            base_path=self._dir,
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse(api_url_name))
        self.client.force_authenticate(user=None)
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_api_success_unauthenticated_user_with_auth_disabled(self):
        """ACCEPTANCE: Validates api success unauthenticated user with auth disabled."""
        api_name = "test_public_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "permission_classes", "[AllowAny]", base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize(
        ("attribute", "value"),
        [
            ("api_request_limit", None),
            ("queue_status_stream_api_limit", None),
        ],
    )
    def test_api_success_optional_attribute_overrides(self, attribute, value):
        """ACCEPTANCE: Validates api success optional attribute overrides."""
        api_name = f"test_acceptance_{attribute}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, attribute, value, base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_api_success_basic_validation_allows_extra_fields(self):
        """ACCEPTANCE: Validates api success basic validation allows extra fields."""
        api_name = "test_basic_extra_fields_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"name": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"name": "John", "unexpected": "extra"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)


@pytest.mark.django_db(transaction=True)
class TestAPIMixinRejection(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates
        self.user = User.objects.create_user(username="testuser", password=pass_code)

    @pytest.mark.parametrize("debug_mode", [False, True])
    def test_rejection_exception_from_run_method(self, settings, caplog, debug_mode):
        """REJECTION: Validates rejection exception from run method."""
        settings.DEBUG = debug_mode
        api_name = f"test_exception_run_{str(debug_mode).lower()}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_run_method(
            self._app,
            api_name,
            '        raise ValueError("Test run exception")',
            base_path=self._dir,
        )
        with caplog.at_level(logging.ERROR):
            response = self.client.get(reverse(api_url_name))
        assert response.status_code == 500
        assert response.data["message"]["code"] == "UNEXPECTED_ERR"

    @pytest.mark.parametrize(
        ("configured_method", "requested_method"),
        [
            ("get", "post"),
            ("post", "get"),
            ("put", "delete"),
            ("delete", "post"),
        ],
    )
    def test_rejection_request_method_mismatch(
        self, configured_method, requested_method
    ):
        """REJECTION: Validates rejection request method mismatch."""
        api_name = f"test_mismatch_{configured_method}_{requested_method}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "method", configured_method, base_path=self._dir
        )
        response = getattr(self.client, requested_method)(reverse(api_url_name))
        assert (
            response.status_code == 400
            and response.data["message"]["code"] == "INVALID_METHOD"
        )

    def test_rejection_unauthenticated_user_with_auth_required(self):
        """REJECTION: Validates rejection unauthenticated user with auth required."""
        api_name = "test_auth_required_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "permission_classes": "[IsAuthenticated]",
                "authentication_classes": "[BasicAuthentication]",
            },
            base_path=self._dir,
        )
        response = self.client.get(reverse(api_url_name))
        assert response.data["message"]["code"] == "NOT_AUTHENTICATED"
        assert response.status_code == 401

    @patch("apps.django_mindoff.components.api_kit.is_ratelimited")
    def test_rejection_api_request_limit_exceeded(self, mock_limit):
        """REJECTION: Validates rejection api request limit exceeded."""
        cache.clear()
        mock_limit.return_value = True
        api_url_name = self._make_api("test_rate_limit_api")
        response = self.client.get(reverse(api_url_name))
        assert response.data["message"]["code"] == "API_RATE_LIMITED"

    def test_rejection_payload_size_exceeded(self):
        """REJECTION: Validates rejection payload size exceeded."""
        api_name = "test_size_exceeded_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 0.001,
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"data": "x" * 10000}, format="json"
        )
        assert response.data["message"]["code"] == "PAYLOAD_TOO_LARGE"

    @pytest.mark.parametrize(
        ("payload", "schema", "validation_mode", "error_code"),
        [
            (
                {"name": "John"},
                '{"name": str, "age": int}',
                "strict",
                "INVALID_PAYLOAD",
            ),
            (
                {"name": "John", "age": "thirty"},
                '{"name": str, "age": int}',
                "strict",
                "INVALID_PAYLOAD",
            ),
        ],
    )
    def test_rejection_payload_validation_strict_errors(
        self, payload, schema, validation_mode, error_code
    ):
        """REJECTION: Validates rejection payload validation strict errors."""
        has_wrong_type = "age" in payload and not isinstance(payload.get("age"), int)
        slug = "wrong_type" if has_wrong_type else "missing_field"
        api_name = f"test_strict_validation_{slug}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": schema,
                "payload_validation": validation_mode,
            },
            base_path=self._dir,
        )
        response = self.client.post(reverse(api_url_name), payload, format="json")
        assert response.data["message"]["code"] == error_code

    def test_rejection_payload_depth_exceeded(self):
        """REJECTION: Validates rejection payload depth exceeded."""
        api_name = "test_depth_exceeded_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"level1": {"level2": {"level3": str}}}',
                "payload_validation": "strict",
                "max_payload_depth": 2,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"level1": {"level2": {"level3": "value"}}},
            format="json",
        )
        assert response.data["message"]["code"] == "INVALID_PAYLOAD"

    def test_rejection_mindoff_validation_error_from_run(self):
        """REJECTION: Validates rejection mindoff validation error from run."""
        api_name = "test_mindoff_validation_error_api"
        api_url_name = self._make_api(api_name)
        run_code = """
            from apps.django_mindoff.components.validation_kit import MindoffValidationError
            raise MindoffValidationError(
                code="VALIDATION_ERR",
                category="warning",
                data={"detail": "Custom validation failure"}
            )
        """
        _modify_api_run_method(self._app, api_name, run_code, base_path=self._dir)
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 400
        assert response.data["message"]["code"] == "VALIDATION_ERR"
        assert response.data["message"]["category"] == "warning"

    @pytest.mark.parametrize(
        (
            "exception_code",
            "exception_import",
            "exception_raise",
            "expected_status",
            "expected_code",
        ),
        [
            (
                "PERMISSION_DENIED",
                "from rest_framework.exceptions import PermissionDenied",
                'raise PermissionDenied("Access denied")',
                403,
                "PERMISSION_DENIED",
            ),
            (
                "RATE_LIMITED",
                "from rest_framework.exceptions import Throttled",
                "raise Throttled(wait=60)",
                429,
                "RATE_LIMITED",
            ),
        ],
    )
    def test_rejection_drf_exceptions_from_run(
        self,
        exception_code,
        exception_import,
        exception_raise,
        expected_status,
        expected_code,
    ):
        """REJECTION: Validates rejection drf exceptions from run."""
        api_name = f"test_{exception_code.lower()}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_run_method(
            self._app,
            api_name,
            f"            {exception_import}\n            {exception_raise}",
            base_path=self._dir,
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == expected_status
        assert response.data["message"]["code"] == expected_code

    def test_rejection_payload_not_allowed_when_schema_none(self):
        """REJECTION: Validates rejection payload not allowed when schema none."""
        api_name = "test_payload_not_allowed_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": None,
                "payload_validation": "strict",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"unexpected": "data"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["message"]["code"] == "PAYLOAD_NOT_ALLOWED"

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)


@pytest.mark.django_db(transaction=True)
class TestAPIMixinBoundary(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    @patch("apps.django_mindoff.components.api_kit.is_ratelimited")
    def test_boundary_api_request_at_exact_limit(self, mock_limit):
        """BOUNDARY: Validates boundary api request at exact limit."""
        mock_limit.return_value = False
        api_url_name = self._make_api("test_limit_boundary_api")
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_payload_size_at_exact_limit(self):
        """BOUNDARY: Validates boundary payload size at exact limit."""
        api_name = "test_size_boundary_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 1,
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"data": "x" * 1048565}, format="json"
        )
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_payload_validation_none_with_mismatched_payload(self):
        """REJECTION: Validates boundary payload validation none with mismatched payload."""
        api_name = "test_validation_none_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"name": str, "age": int}',
                "payload_validation": None,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"name": "John", "age": "not_an_int"},
            format="json",
        )
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_payload_depth_exact(self):
        """BOUNDARY: Validates boundary payload depth exact."""
        api_name = "test_depth_exact_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "payload_schema": '{"level1": {"level2": {"level3": str}}}',
                "payload_validation": "strict",
                "max_payload_depth": 3,
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"level1": {"level2": {"level3": "value"}}},
            format="json",
        )
        assert response.data["message"]["code"] == "SUCCESS"

    @pytest.mark.parametrize(
        ("rate_value", "description"),
        [
            ("1/s", "minimal_valid_rate"),
            ("9999/d", "large_but_valid_rate"),
        ],
    )
    def test_boundary_valid_rate_limits(self, rate_value, description):
        """BOUNDARY: Validates boundary valid rate limits."""
        api_name = f"test_boundary_rate_{description}_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attribute(
            self._app, api_name, "api_request_limit", rate_value, base_path=self._dir
        )
        response = self.client.get(reverse(api_url_name))
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_float_max_payload_size(self):
        """BOUNDARY: Validates boundary float max payload size."""
        api_name = "test_boundary_float_size_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 0.5,
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name), {"data": "x" * 1000}, format="json"
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def test_boundary_missing_content_length_header(self):
        """REJECTION: Validates boundary missing content length header."""
        api_name = "test_boundary_missing_content_length_api"
        api_url_name = self._make_api(api_name)
        _modify_api_attributes(
            self._app,
            api_name,
            {
                "method": "post",
                "max_payload_size": 1,
                "payload_schema": '{"data": str}',
                "payload_validation": "basic",
            },
            base_path=self._dir,
        )
        response = self.client.post(
            reverse(api_url_name),
            {"data": "small"},
            format="json",
            CONTENT_LENGTH="",
        )
        assert response.status_code == 200
        assert response.data["message"]["code"] == "SUCCESS"

    def _make_api(self, api_name: str) -> str:
        return _create_test_api(self._app, api_name, self._dir, self._tmpl)


class TestApiKitAndChecksCoverage:

    def test_queue_service_unavailable_error_detection(self):
        """REJECTION: Validates queue service unavailable error detection."""
        assert _is_queue_service_unavailable_error(ConnectionError("offline")) is True
        assert (
            _is_queue_service_unavailable_error(RuntimeError("cannot connect to redis"))
            is True
        )
        assert _is_queue_service_unavailable_error(RuntimeError("bad payload")) is False

    def test_mindoff_api_run_default_raises_not_implemented(self):
        """REJECTION: Validates mindoff api run default raises not implemented."""
        mixin = MindoffAPIMixin()
        with pytest.raises(NotImplementedError):
            mixin.run(request=None)

    def test_dispatch_ensure_response_sets_renderer_when_missing(self):
        """REJECTION: Validates dispatch ensure response sets renderer when missing."""
        mixin = MindoffAPIMixin()
        mixin.get_renderer_context = lambda: {}
        resp = Response({"ok": True}, status=200)
        out = mixin._dispatch_ensure_response_is_rendered(resp)
        assert out.accepted_media_type == "application/json"
        assert hasattr(out, "accepted_renderer")

    def test_dispatch_exception_path_wraps_response(self):
        """ACCEPTANCE: Validates dispatch exception path wraps response."""
        from django.test import RequestFactory

        mixin = MindoffAPIMixin()
        mixin.get_renderer_context = lambda: {}
        req = RequestFactory().get("/x")

        with patch(
            "rest_framework.views.APIView.dispatch", side_effect=RuntimeError("boom")
        ):
            out = mixin.dispatch(req)

        assert out.status_code == 500
        assert out.data["message"]["code"] == "UNEXPECTED_ERR"
        assert hasattr(out, "accepted_renderer")

    @patch("apps.django_mindoff.components.api_kit.enqueue_process")
    def test_handle_request_logic_reraises_non_queue_backend_errors(self, mock_enqueue):
        """REJECTION: Validates handle request logic reraises non queue backend errors."""
        mock_enqueue.side_effect = ValueError("unexpected worker error")

        class QueueOnlyMixin(MindoffAPIMixin):
            process_mode = "queue"
            progress_steps = None

        req = type(
            "Req",
            (),
            {
                "FILES": {},
                "build_absolute_uri": staticmethod(lambda s: f"http://x{s}"),
            },
        )()
        with pytest.raises(ValueError, match="unexpected worker error"):
            QueueOnlyMixin()._handle_request_logic(req)

    def test_handle_exception_authentication_failed_branch(self):
        """REJECTION: Validates handle exception authentication failed branch."""
        mixin = MindoffAPIMixin()
        resp = mixin.handle_exception(AuthenticationFailed("bad auth"))
        assert resp.status_code == 401
        assert resp.data["message"]["code"] == "AUTHENTICATION_FAILED"

    @pytest.mark.parametrize(
        ("exc", "expected_code"),
        [
            (
                MindoffValidationError(
                    message="bad",
                    code="VALIDATION_ERR",
                    category="warning",
                    data={"a": 1},
                ),
                "VALIDATION_ERR",
            ),
            (NotAuthenticated("x"), "NOT_AUTHENTICATED"),
            (AuthenticationFailed("x"), "AUTHENTICATION_FAILED"),
            (PermissionDenied("x"), "PERMISSION_DENIED"),
            (Throttled(wait=1), "RATE_LIMITED"),
            (RuntimeError("x"), "UNEXPECTED_ERR"),
        ],
    )
    def test_api_guardian_maps_errors(self, exc, expected_code):
        """REJECTION: Validates api guardian maps errors."""

        @api_guardian
        def protected_view(_request):
            raise exc

        resp = protected_view(object())
        assert resp.data["message"]["code"] == expected_code

    def test_base_version_router_invalid_version_response_shape(self):
        """REJECTION: Validates base version router invalid version response shape."""
        from django.test import RequestFactory
        from ....components._api_kit.api_router import APIVersionRouter

        class Router(APIVersionRouter):
            VERSION_MAP = {1: object}

        req = RequestFactory().get("/x")
        resp = Router()(req, version=99)
        assert resp.data["message"]["code"] == "INVALID_API_VERSION"
        assert resp.data["data"]["available_versions"] == [1]

    @patch("apps.django_mindoff.components._api_kit.api_router.settings")
    def test_base_version_router_view_cache_enabled(self, mock_settings):
        """ACCEPTANCE: Validates base version router view cache enabled."""
        from django.test import RequestFactory
        from ....components._api_kit.api_router import APIVersionRouter

        mock_settings.MINDOFF_USE_VIEW_CACHE = True
        calls = {"as_view": 0}

        class FakeView:
            @classmethod
            def as_view(cls):
                calls["as_view"] += 1
                return lambda request, *args, **kwargs: mo_response_kit.json_response(
                    code="SUCCESS", category="success"
                )

        class Router(APIVersionRouter):
            VERSION_MAP = {1: FakeView}

        req = RequestFactory().get("/x")
        router = Router()
        r1 = router(req, version=1)
        r2 = router(req, version=1)
        assert r1.status_code == 200 and r2.status_code == 200
        assert calls["as_view"] == 1

    @patch("apps.django_mindoff.components._api_kit.api_router.settings")
    def test_base_version_router_view_cache_disabled(self, mock_settings):
        """ACCEPTANCE: Validates base version router view cache disabled."""
        from django.test import RequestFactory
        from ....components._api_kit.api_router import APIVersionRouter

        mock_settings.MINDOFF_USE_VIEW_CACHE = False
        calls = {"as_view": 0}

        class FakeView:
            @classmethod
            def as_view(cls):
                calls["as_view"] += 1
                return lambda request, *args, **kwargs: mo_response_kit.json_response(
                    code="SUCCESS", category="success"
                )

        class Router(APIVersionRouter):
            VERSION_MAP = {1: FakeView}

        req = RequestFactory().get("/x")
        router = Router()
        router(req, version=1)
        router(req, version=1)
        assert calls["as_view"] == 2

    def test_checks_dependency_error_short_circuit(self, monkeypatch):
        """REJECTION: Validates checks dependency error short circuit."""
        import apps.django_mindoff.checks as checks_module

        monkeypatch.setattr(
            checks_module, "_get_missing_dependencies", lambda: ["dramatiq", "redis"]
        )
        errors = checks_module.check_mindoff_api_configs(None)
        assert errors
        assert errors[0].id == "django_mindoff.DEPENDENCY_ERR"
        assert "dramatiq, redis" in errors[0].msg

    def test_checks_import_failure_returns_dependency_error(self, monkeypatch):
        """REJECTION: Validates checks import failure returns dependency error."""
        import builtins
        import apps.django_mindoff.checks as checks_module

        monkeypatch.setattr(checks_module, "_get_missing_dependencies", lambda: [])
        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith("components.api_kit"):
                raise ImportError("forced import failure")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        errors = checks_module.check_mindoff_api_configs(None)
        assert errors
        assert errors[0].id == "django_mindoff.DEPENDENCY_ERR"
        assert "Failed to import django-mindoff API components" in errors[0].msg

    def test_validate_view_class_reports_api_config_error_id(self):
        """REJECTION: Validates validate view class reports api config error id."""
        import apps.django_mindoff.checks as checks_module

        class BadView:
            def validate_api_configuration(self):
                exc = RuntimeError("broken config")
                exc.code = "API_CONFIG_ERR"
                raise exc

        errors = []
        checks_module._validate_view_class(BadView, errors)
        assert errors
        assert errors[0].id == "django_mindoff.API_CONFIG_ERR"

    def test_get_missing_dependencies_detects_modules(self, monkeypatch):
        """REJECTION: Validates get missing dependencies detects modules."""
        import apps.django_mindoff.checks as checks_module

        monkeypatch.setattr(
            checks_module,
            "REQUIRED_INTEGRATION_DEPENDENCIES",
            [("pkg_ok", "json"), ("pkg_missing", "__definitely_missing_mod__")],
        )
        missing = checks_module._get_missing_dependencies()
        assert missing == ["pkg_missing"]

    def test_checks_skips_unimportable_api_modules(self, monkeypatch, tmp_path):
        """ACCEPTANCE: Validates checks skips unimportable api modules."""
        import apps.django_mindoff.checks as checks_module

        class FakeAppConfig:
            path = str(tmp_path / "fake_app")
            name = "fake_app"

        (tmp_path / "fake_app" / "apis").mkdir(parents=True)
        monkeypatch.setattr(checks_module, "_get_missing_dependencies", lambda: [])
        monkeypatch.setattr(
            checks_module.django_apps, "get_app_configs", lambda: [FakeAppConfig()]
        )
        monkeypatch.setattr(
            checks_module.pkgutil,
            "walk_packages",
            lambda path, prefix, onerror: [(None, f"{prefix}broken_api", False)],
        )
        monkeypatch.setattr(
            checks_module.importlib,
            "import_module",
            lambda name: iter([]).throw(RuntimeError("broken import")),
        )

        errors = checks_module.check_mindoff_api_configs(None)
        assert errors == []


@pytest.fixture(scope="session")
def _api_templates(tmp_path_factory):
    import shutil
    from django.apps import apps as django_apps
    from django.conf import settings as django_settings
    from django.test import override_settings
    from django.urls import clear_url_caches as _clear
    from ....components.managers._create_api import DjangoApiCreator
    from ....components.managers._create_app import DjangoAppCreator

    tmp_root = Path(tmp_path_factory.mktemp("canonical_bootstrap"))
    sys.path.insert(0, str(tmp_root))
    app_creator = DjangoAppCreator(_CANONICAL_APP, isolated=True)
    app_creator.project_root = tmp_root
    app_creator.app_dir = str(tmp_root / _CANONICAL_APP)
    app_creator.settings_path = tmp_root / "dummy_settings.py"
    app_creator.urls_path = tmp_root / "dummy_urls.py"
    app_creator.run()
    ov = override_settings(
        INSTALLED_APPS=list(django_settings.INSTALLED_APPS) + [_CANONICAL_APP]
    )
    ov.enable()
    django_apps.set_installed_apps(django_settings.INSTALLED_APPS)
    DjangoApiCreator(
        api_path=f"{_CANONICAL_APP}/{_CANONICAL_API}",
        base_path=tmp_root,
    ).run()
    app_root = tmp_root / _CANONICAL_APP

    def _fix_imports(text: str) -> str:
        return re.sub(r"\bfrom django_mindoff\b", "from apps.django_mindoff", text)

    templates = {
        "api": _fix_imports((app_root / "apis" / f"{_CANONICAL_API}.py").read_text()),
        "views": _fix_imports((app_root / "views.py").read_text()),
        "urls": (app_root / "urls.py").read_text(),
    }
    ov.disable()
    _clear()
    to_remove = [
        mod
        for mod in list(sys.modules.keys())
        if mod == _CANONICAL_APP or mod.startswith(f"{_CANONICAL_APP}.")
    ]
    for mod in to_remove:
        sys.modules.pop(mod, None)
    sys.path[:] = [p for p in sys.path if p != str(tmp_root)]
    shutil.rmtree(tmp_root, ignore_errors=True)
    django_apps.app_configs.pop(_CANONICAL_APP, None)
    django_apps.clear_cache()
    django_apps.populate(django_settings.INSTALLED_APPS)
    return templates


def _create_test_api(
    app_name: str,
    api_name: str,
    base_path: Path,
    templates: dict,
    url_patterns: list = None,
) -> str:
    pascal = _pascal(api_name)
    human = _human(api_name)
    url_name = f"{app_name}__{api_name}"
    canonical_url_name = f"{_CANONICAL_APP}__{_CANONICAL_API}"

    def _sub(text: str) -> str:
        return (
            text.replace(canonical_url_name, url_name)
            .replace(_CANONICAL_ROUTER_CLASS, f"{pascal}Router")
            .replace(_CANONICAL_ROUTER_INST, f"{api_name}_router")
            .replace(_CANONICAL_CLASS, f"{pascal}V1APIView")
            .replace(_CANONICAL_HUMAN, human)
            .replace(_CANONICAL_APP, app_name)
            .replace(_CANONICAL_API, api_name)
        )

    app_root = base_path / app_name
    __write_api_file(app_root, api_name, _sub(templates["api"]))
    __write_views_file(app_root, _sub(templates["views"]))
    __write_urls_file(
        app_root, app_name, api_name, url_patterns, _sub(templates["urls"])
    )

    _reload_api_modules(app_name, api_name)
    clear_url_caches()
    return url_name


def _modify_api_attributes(
    app_name: str,
    api_name: str,
    attributes: dict,
    base_path: Path,
):
    api_file = base_path / app_name / "apis" / f"{api_name}.py"
    if not api_file.exists():
        raise FileNotFoundError(f"API file not found at {api_file}")

    content = api_file.read_text()

    for attribute, value in attributes.items():
        if value is None:
            value_str = "None"
        elif isinstance(value, bool):
            value_str = str(value)
        elif isinstance(value, (list, dict)):
            value_str = str(value)
        elif isinstance(value, str):
            code_indicators = ["[", "{", "None"]
            if (
                any(value.strip().startswith(c) for c in code_indicators)
                or value.strip() == "None"
            ):
                value_str = value
            else:
                value_str = f'"{value}"'
        else:
            value_str = str(value)

        typed_pattern = rf"(\s+{attribute}\s*:\s*[^=]+=\s*)(.+?)(\s*(?:#|$))"
        untyped_pattern = rf"(\s+{attribute}\s*=\s*)(.+?)(\s*(?:#|$))"

        if re.search(typed_pattern, content, flags=re.MULTILINE):
            content = re.sub(
                typed_pattern, rf"\g<1>{value_str}\g<3>", content, flags=re.MULTILINE
            )
        elif re.search(untyped_pattern, content, flags=re.MULTILINE):
            content = re.sub(
                untyped_pattern, rf"\g<1>{value_str}\g<3>", content, flags=re.MULTILINE
            )
        else:
            insert_at = re.search(r"^ {4}def\s+\w+\(", content, flags=re.MULTILINE)
            insert_pos = insert_at.start() if insert_at else len(content)
            line = f"    {attribute} = {value_str}\n"
            prefix = content[:insert_pos]
            suffix = content[insert_pos:]
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            content = prefix + line + ("\n" if not line.endswith("\n\n") else "") + suffix

    api_file.write_text(content)


def _modify_api_attribute(
    app_name: str,
    api_name: str,
    attribute: str,
    value,
    base_path: Path,
):
    _modify_api_attributes(app_name, api_name, {attribute: value}, base_path=base_path)


def _modify_api_run_method(
    app_name: str,
    api_name: str,
    run_code: str,
    base_path: Path,
):
    api_file = base_path / app_name / "apis" / f"{api_name}.py"
    if not api_file.exists():
        raise FileNotFoundError(f"API file not found at {api_file}")

    content = api_file.read_text()
    pattern = (
        r"(def run\(self, request, \*args, \*\*kwargs\):)"
        r"([\s\S]*?)"
        r"(?=\n {4}def |\nclass |\Z)"
    )
    content = re.sub(pattern, rf"\1\n{run_code}\n", content, flags=re.DOTALL)
    api_file.write_text(content)


def _pascal(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_"))


def _human(snake: str) -> str:
    return " ".join(w.capitalize() for w in snake.split("_"))


def _reload_api_modules(app_name: str, api_name: str):
    for mod in (f"{app_name}.views", f"{app_name}.apis.{api_name}"):
        sys.modules.pop(mod, None)


def __write_api_file(app_root: Path, api_name: str, content: str) -> None:
    apis_dir = app_root / "apis"
    apis_dir.mkdir(parents=True, exist_ok=True)
    init = apis_dir / "__init__.py"
    if not init.exists():
        init.write_text("")
    (apis_dir / f"{api_name}.py").write_text(content)


def __write_views_file(app_root: Path, new_views: str) -> None:
    def _split_imports_and_code(text: str) -> tuple[list[str], list[str]]:
        import_lines, code_lines = [], []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                import_lines.append(s)
            else:
                code_lines.append(line)
        return import_lines, code_lines

    views_path = app_root / "views.py"
    if not views_path.exists():
        views_path.write_text(new_views)
        return

    import_lines, code_lines = _split_imports_and_code(new_views)
    existing = views_path.read_text()
    existing_set = set(existing.splitlines())
    missing_imports = [line for line in import_lines if line not in existing_set]

    views_path.write_text(
        existing.rstrip()
        + ("\n" + "\n".join(missing_imports) if missing_imports else "")
        + "\n\n"
        + "\n".join(code_lines)
        + "\n"
    )


def __write_urls_file(
    app_root: Path,
    app_name: str,
    api_name: str,
    url_patterns: list,
    fallback_content: str,
) -> None:
    def _ensure_csrf_exempt_imported(text: str) -> str:
        if "csrf_exempt" in text:
            return text
        return "from django.views.decorators.csrf import csrf_exempt\n" + text

    urls_path = app_root / "urls.py"
    url_segment = url_patterns[0] if url_patterns else f"{api_name}/"
    url_name = f"{app_name}__{api_name}"
    new_entry = (
        f"    path('{url_segment}', "
        f"csrf_exempt(views.{api_name}_router), "
        f"name='{url_name}'),"
    )
    if not urls_path.exists():
        content = _ensure_csrf_exempt_imported(fallback_content)
        urls_path.write_text(content)
        return
    urls_text = _ensure_csrf_exempt_imported(urls_path.read_text())
    urls_text = re.sub(r"(\])", f"{new_entry}\n\\1", urls_text, count=1)
    urls_path.write_text(urls_text)
