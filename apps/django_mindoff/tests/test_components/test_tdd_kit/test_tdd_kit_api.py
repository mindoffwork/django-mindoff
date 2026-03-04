import uuid
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest

from ....components.tdd_kit import MindoffTestCase, MindoffRouterTestCase


# ════════════════════════════════════════════════════════════════════════
# 🚂 TestMoCallApi
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMoCallApi(MindoffTestCase):
    """Tests for the mo_call_api fixture."""

    API_URL_NAME = "tdd_test__sample_api"

    def _patched_call(self, api_cls, http_method, **call_kwargs):
        """Patch resolver + reverse + client method; return (response, mock)."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=api_cls,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(
                self.client, http_method, return_value=_make_raw_response()
            ) as mock,
        ):
            response = self.mo_call_api(self.API_URL_NAME, **call_kwargs)
        return response, mock

    # ✅ ACCEPTANCE ───────────────────────────────────────────────────────

    def test_returns_raw_response_object(self):
        """mo_call_api returns an object with .status_code, .headers, .content."""
        response, _ = self._patched_call(_make_api_cls(method="get"), "get")
        assert hasattr(response, "status_code")
        assert hasattr(response, "headers")
        assert hasattr(response, "content")

    @pytest.mark.parametrize("method", ["get", "delete"])
    def test_get_and_delete_send_no_payload(self, method):
        """GET and DELETE never include a data kwarg."""
        _, mock = self._patched_call(_make_api_cls(method=method), method)
        assert "data" not in (mock.call_args.kwargs or {})

    @pytest.mark.parametrize("method", ["post", "put", "patch"])
    def test_mutation_methods_send_provided_payload(self, method):
        """POST, PUT, PATCH send the developer-supplied payload as-is."""
        custom = {"name": "Alice", "age": 30}
        _, mock = self._patched_call(
            _make_api_cls(method=method), method, payload=custom
        )
        sent = (
            mock.call_args.kwargs["data"]
            if "data" in mock.call_args.kwargs
            else mock.call_args.args[1]
        )
        assert sent == custom

    @pytest.mark.parametrize("method", ["post", "put", "patch"])
    def test_mutation_methods_with_no_payload_send_empty_dict(self, method):
        """POST, PUT, PATCH with no payload provided → empty dict sent."""
        _, mock = self._patched_call(_make_api_cls(method=method), method)
        sent = (
            mock.call_args.kwargs["data"]
            if "data" in mock.call_args.kwargs
            else mock.call_args.args[1]
        )
        assert sent == {}

    def test_url_kwargs_passed_to_reverse(self):
        """url_kwargs supplied by caller are forwarded to reverse."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ) as mock_rev,
            patch.object(self.client, "get", return_value=_make_raw_response()),
        ):
            self.mo_call_api(self.API_URL_NAME, url_kwargs={"pk": 42})
        mock_rev.assert_called_once_with(self.API_URL_NAME, kwargs={"pk": 42})

    def test_no_url_kwargs_calls_reverse_with_empty_dict(self):
        """No url_kwargs supplied → reverse called with empty kwargs dict."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ) as mock_rev,
            patch.object(self.client, "get", return_value=_make_raw_response()),
        ):
            self.mo_call_api(self.API_URL_NAME)
        mock_rev.assert_called_once_with(self.API_URL_NAME, kwargs={})

    def test_query_params_appended_to_url(self):
        """query_params supplied by caller are appended to the URL."""
        _, mock = self._patched_call(
            _make_api_cls(method="get"), "get", query_params={"page": 2, "search": "hi"}
        )
        url = mock.call_args.args[0]
        assert "page=2" in url
        assert "search=hi" in url

    def test_no_query_params_omits_query_string(self):
        """No query_params → URL has no query string."""
        _, mock = self._patched_call(_make_api_cls(method="get"), "get")
        url = mock.call_args.args[0]
        assert "?" not in url

    def test_user_triggers_force_authenticate(self):
        """Passing user= calls client.force_authenticate with that user."""
        fake_user = MagicMock()
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", return_value=_make_raw_response()),
            patch.object(self.client, "force_authenticate") as mock_auth,
        ):
            self.mo_call_api(self.API_URL_NAME, user=fake_user)
        mock_auth.assert_called_once_with(user=fake_user)

    def test_no_user_skips_force_authenticate(self):
        """user=None → force_authenticate never called."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", return_value=_make_raw_response()),
            patch.object(self.client, "force_authenticate") as mock_auth,
        ):
            self.mo_call_api(self.API_URL_NAME)
        mock_auth.assert_not_called()

    def test_accept_header_always_json_and_custom_headers_merged(self):
        """Accept: application/json always present; extra headers merged, not replaced."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", return_value=_make_raw_response()) as mock,
        ):
            self.mo_call_api(self.API_URL_NAME, headers={"X-Custom": "value"})
        sent = mock.call_args.kwargs.get("headers", {})
        assert sent.get("Accept") == "application/json"
        assert sent.get("X-Custom") == "value"

    def test_queue_mode_api_returns_direct_response(self):
        """Queue-mode APIs are forced into direct mode during tests and return run() output directly."""
        direct_response = _make_raw_response(
            body={"message": {"code": "SUCCESS"}, "data": {"ok": 1}}
        )

        def _fake_reverse(name, kwargs=None, args=None):
            if name == self.API_URL_NAME:
                return "/enqueue/"
            raise AssertionError(f"Unexpected reverse call: {name}")

        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get", process_mode="queue"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse",
                side_effect=_fake_reverse,
            ),
            patch.object(self.client, "get", return_value=direct_response) as mock_get,
        ):
            response = self.mo_call_api(self.API_URL_NAME)

        assert response is direct_response
        assert mock_get.call_count == 1

    def test_queue_mode_api_sets_and_clears_force_direct_flag(self):
        """_test_force_direct.active is True during dispatch and always cleaned up after."""
        from apps.django_mindoff.components.api_kit import _test_force_direct

        observed_during: list[bool] = []
        direct_response = _make_raw_response(
            body={"message": {"code": "SUCCESS"}, "data": {}}
        )

        def _capture_flag(*args, **kwargs):
            observed_during.append(getattr(_test_force_direct, "active", False))
            return direct_response

        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get", process_mode="queue"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse",
                return_value="/enqueue/",
            ),
            patch.object(self.client, "get", side_effect=_capture_flag),
        ):
            self.mo_call_api(self.API_URL_NAME)

        assert observed_during == [True]
        assert getattr(_test_force_direct, "active", False) is False

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "method, payload",
        [
            ("get", {"bad": "payload"}),
            ("delete", {"bad": "payload"}),
        ],
    )
    def test_get_and_delete_with_payload_raises(self, method, payload):
        """GET and DELETE with a payload → raises."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method=method),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
        ):
            with pytest.raises(Exception):
                self.mo_call_api(self.API_URL_NAME, payload=payload)


# ════════════════════════════════════════════════════════════════════════
# 🚂 TestMoAssertApiResponse
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMoAssertApiResponse(MindoffTestCase):
    """Tests for the mo_assert_api_response fixture."""

    API_URL_NAME = "tdd_test__assert_api"

    def _assert(self, response, **kwargs):
        self.mo_assert_api_response(
            api_url_name=self.API_URL_NAME,
            response=response,
            **kwargs,
        )

    # ✅ ACCEPTANCE — status code ─────────────────────────────────────────

    def test_correct_status_code_passes(self):
        self._assert(_make_raw_response())

    def test_wrong_status_code_fails(self):
        resp = _make_raw_response()
        resp.status_code = 404
        with pytest.raises(AssertionError, match="404"):
            self._assert(resp)

    def test_custom_expected_status_code_passes(self):
        resp = _make_raw_response(content_type="text/plain", body="bad request")
        resp.status_code = 400
        self._assert(resp, expected_status_code=400, expected_response_type="plain")

    def test_custom_expected_status_code_mismatch_fails(self):
        resp = _make_raw_response()
        with pytest.raises(AssertionError, match="200"):
            self._assert(resp, expected_status_code=201)

    # ✅ ACCEPTANCE — content-type matching ───────────────────────────────

    def test_json_content_type_passes(self):
        self._assert(_make_raw_response(content_type="application/json"))

    def test_wrong_content_type_for_json_fails(self):
        resp = _make_raw_response(content_type="text/html", body="<html/>")
        with pytest.raises(AssertionError, match="JSON"):
            self._assert(resp, expected_response_type="json")

    @pytest.mark.parametrize(
        "response_type, content_type, body",
        [
            ("binary", "application/octet-stream", b"\x00\x01\x02"),
            ("html", "text/html", "<html><body>ok</body></html>"),
            ("plain", "text/plain", "just some text"),
        ],
    )
    def test_valid_response_types_pass(self, response_type, content_type, body):
        resp = _make_raw_response(content_type=content_type, body=body)
        self._assert(resp, expected_response_type=response_type)

    @pytest.mark.parametrize(
        "response_type, content_type, body, match",
        [
            ("html", "application/json", None, "HTML"),
            ("plain", "application/json", None, "plain"),
            ("binary", "text/plain", b"\x00", "Content-Type"),
        ],
    )
    def test_mismatched_content_type_fails(
        self, response_type, content_type, body, match
    ):
        resp = _make_raw_response(content_type=content_type, body=body)
        with pytest.raises(AssertionError, match=match):
            self._assert(resp, expected_response_type=response_type)

    def test_others_response_type_skips_content_type_assertion(self):
        """expected_response_type='others' → only status code checked."""
        resp = _make_raw_response(
            content_type="application/x-custom-format", body=b"\xde\xad\xbe\xef"
        )
        self._assert(resp, expected_response_type="others")

    def test_binary_empty_content_is_valid_bytes(self):
        resp = _make_raw_response(content_type="application/octet-stream", body=b"")
        resp.content = b""
        self._assert(resp, expected_response_type="binary")

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    def test_none_response_fails(self):
        with pytest.raises(AssertionError, match="no response"):
            self.mo_assert_api_response(api_url_name=self.API_URL_NAME, response=None)

    @pytest.mark.parametrize("invalid_type", ["JSON", "PLAIN", "Html", "xml", "file"])
    def test_invalid_expected_response_type_raises(self, invalid_type):
        from typeguard import TypeCheckError

        with pytest.raises(TypeCheckError):
            self.mo_assert_api_response(
                api_url_name=self.API_URL_NAME,
                response=_make_raw_response(),
                expected_response_type=invalid_type,
            )


# ════════════════════════════════════════════════════════════════════════
# 🚂 TestMoCreateUser
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMoCreateUser(MindoffTestCase):
    """Tests for the mo_create_user fixture."""

    # ✅ ACCEPTANCE ───────────────────────────────────────────────────────

    def test_creates_user_with_auto_username(self):
        """No username → baker generates one; user is persisted."""
        from django.contrib.auth import get_user_model

        user = self.mo_create_user()
        assert user.pk is not None
        assert get_user_model().objects.filter(pk=user.pk).exists()

    def test_creates_user_with_explicit_username(self):
        """Explicit username → user created with that username."""
        user = self.mo_create_user(username="alice")
        assert user.username == "alice"

    def test_password_is_set_and_usable(self):
        """Created user has a hashed, usable password."""
        password = uuid.uuid4().hex
        user = self.mo_create_user(username="bob", password=password)
        assert user.check_password(password)

    def test_duplicate_username_returns_existing_user(self):
        """Calling mo_create_user twice with the same username returns the same object."""
        user1 = self.mo_create_user(username="carol")
        user2 = self.mo_create_user(username="carol")
        assert user1.pk == user2.pk

    def test_extra_fields_are_applied(self):
        """kwargs beyond username/password are forwarded to baker.make."""
        user = self.mo_create_user(username="dave", email="dave@example.com")
        assert user.email == "dave@example.com"

    # 🚧 BOUNDARY ────────────────────────────────────────────────────────

    def test_none_password_skips_set_password(self):
        """password=None → set_password never called; user is still created."""
        user = self.mo_create_user(username="nopw", password=None)
        assert user.pk is not None


# ════════════════════════════════════════════════════════════════════════
# 🚂 TestMindoffRouterTestCase
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMindoffRouterTestCase(MindoffTestCase):
    """
    Unit-tests for the two built-in router assertions in MindoffRouterTestCase,
    exercised directly without subclassing (avoids needing a real router module).
    """

    def _make_router_case(self, version_map: dict):
        """Build a throwaway MindoffRouterTestCase instance wired to a fake router."""

        class FakeRouter:
            VERSION_MAP = version_map

            def __call__(self, request, version):
                cls = version_map.get(version)
                if cls is None:
                    from rest_framework.response import Response

                    return Response(
                        {
                            "message": {"code": "INVALID_API_VERSION"},
                            "data": {"available_versions": list(version_map.keys())},
                        },
                        status=404,
                    )
                return cls.as_view()(request)

        class ConcreteRouterCase(MindoffRouterTestCase):
            app_module = "__fake__"
            router_function_name = "fake_router"

        instance = ConcreteRouterCase()
        instance.router = FakeRouter()
        instance.version_map = version_map
        return instance

    # ✅ ACCEPTANCE ───────────────────────────────────────────────────────

    def test_every_version_dispatches_to_correct_class(self):
        """Each VERSION_MAP entry calls as_view() exactly once on the right class."""
        from unittest.mock import MagicMock, patch
        from django.test import RequestFactory

        class V1View:
            pass

        class V2View:
            pass

        case = self._make_router_case({1: V1View, 2: V2View})
        # Delegate to the built-in test — it should not raise
        case.test_every_version_dispatches_to_correct_class()

    def test_unknown_version_returns_404_with_correct_body(self):
        """Version absent from VERSION_MAP → 404 with INVALID_API_VERSION body."""

        class V1View:
            pass

        case = self._make_router_case({1: V1View})
        case.test_unknown_version_returns_404_with_correct_body()

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    def test_wrong_version_dispatches_to_wrong_class_fails(self):
        """If router dispatches to the wrong class, the assertion fails."""
        from unittest.mock import patch, MagicMock
        from django.test import RequestFactory

        class RightView:
            pass

        class WrongView:
            pass

        # Build a router that always dispatches to WrongView regardless of version
        class BadRouter:
            VERSION_MAP = {1: RightView}

            def __call__(self, request, version):
                return WrongView.as_view()(request)

        class ConcreteRouterCase(MindoffRouterTestCase):
            app_module = "__fake__"
            router_function_name = "bad_router"

        instance = ConcreteRouterCase()
        instance.router = BadRouter()
        instance.version_map = {1: RightView}

        with pytest.raises(AssertionError):
            instance.test_every_version_dispatches_to_correct_class()

    def test_correct_404_body_missing_code_fails(self):
        """Router returning 404 without INVALID_API_VERSION code → assertion fails."""
        from rest_framework.response import Response

        class V1View:
            pass

        class BadRouter:
            VERSION_MAP = {1: V1View}

            def __call__(self, request, version):
                # Returns 404 but with wrong body structure
                return Response(
                    {"message": {"code": "WRONG_CODE"}, "data": {}}, status=404
                )

        class ConcreteRouterCase(MindoffRouterTestCase):
            app_module = "__fake__"
            router_function_name = "bad_router"

        instance = ConcreteRouterCase()
        instance.router = BadRouter()
        instance.version_map = {1: V1View}

        with pytest.raises((AssertionError, KeyError)):
            instance.test_unknown_version_returns_404_with_correct_body()


# ════════════════════════════════════════════════════════════════════════
# 🔧 Shared helpers
# ════════════════════════════════════════════════════════════════════════


def _make_api_cls(*, method="get", process_mode="direct"):
    class FakeAPI:
        pass

    FakeAPI.method = method
    FakeAPI.process_mode = process_mode
    return FakeAPI


def _make_raw_response(
    *,
    status_code=200,
    content_type="application/json",
    body=None,
):
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type}
    if isinstance(body, bytes):
        resp.content = body
        resp.json.side_effect = Exception("not JSON")
    elif isinstance(body, str):
        resp.content = body.encode()
        resp.json.side_effect = Exception("not JSON")
    elif body is None:
        resp.content = b"{}"
        resp.json.return_value = {}
    else:
        import json

        resp.content = json.dumps(body).encode()
        resp.json.return_value = body
    return resp
