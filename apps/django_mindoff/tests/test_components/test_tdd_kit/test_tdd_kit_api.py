import uuid
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest

from ....components.tdd_kit import MindoffTestCase


@pytest.mark.django_db
class TestMoTestApi(MindoffTestCase):
    """Tests for the mo_test_api fixture."""

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
            response = self.mo_test_api(self.API_URL_NAME, **call_kwargs)
        return response, mock

    # ✅ ACCEPTANCE ───────────────────────────────────────────────────────

    def test_returns_raw_response_object(self):
        """mo_test_api returns an object with .status_code, .headers, .content."""
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
            self.mo_test_api(self.API_URL_NAME, url_kwargs={"pk": 42})
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
            self.mo_test_api(self.API_URL_NAME)
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
            self.mo_test_api(self.API_URL_NAME, user=fake_user)
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
            self.mo_test_api(self.API_URL_NAME)
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
            self.mo_test_api(self.API_URL_NAME, headers={"X-Custom": "value"})
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
            response = self.mo_test_api(self.API_URL_NAME)

        assert response is direct_response
        # Only one GET — no queue polling, no detail URL fetch
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
            self.mo_test_api(self.API_URL_NAME)

        # Flag was active during the request
        assert observed_during == [True]
        # Flag is cleaned up after
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
                self.mo_test_api(self.API_URL_NAME, payload=payload)


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
        """Default expected_status_code=200 with a 200 response → passes."""
        self._assert(_make_raw_response())

    def test_wrong_status_code_fails(self):
        """Response status_code != expected → AssertionError containing the actual code."""
        resp = _make_raw_response()
        resp.status_code = 404
        with pytest.raises(AssertionError, match="404"):
            self._assert(resp)

    def test_custom_expected_status_code_passes(self):
        """expected_status_code=400 with a matching response → passes."""
        resp = _make_raw_response(content_type="text/plain", body="bad request")
        resp.status_code = 400
        self._assert(resp, expected_status_code=400, expected_response_type="plain")

    def test_custom_expected_status_code_mismatch_fails(self):
        """expected_status_code=201 but response is 200 → AssertionError."""
        resp = _make_raw_response()
        with pytest.raises(AssertionError, match="200"):
            self._assert(resp, expected_status_code=201)

    # ✅ ACCEPTANCE — response type content-type matching ─────────────────

    def test_json_content_type_passes(self):
        """expected_response_type='json' with application/json Content-Type → passes."""
        self._assert(_make_raw_response(content_type="application/json"))

    def test_wrong_content_type_for_json_fails(self):
        """expected_response_type='json' but Content-Type is text/html → AssertionError."""
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
        """Each supported response_type passes with the matching Content-Type."""
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
        """Mismatched Content-Type for any typed response → AssertionError."""
        resp = _make_raw_response(content_type=content_type, body=body)
        with pytest.raises(AssertionError, match=match):
            self._assert(resp, expected_response_type=response_type)

    def test_others_response_type_skips_content_type_assertion(self):
        """expected_response_type='others' → only status code checked, content-type ignored."""
        resp = _make_raw_response(
            content_type="application/x-custom-format", body=b"\xde\xad\xbe\xef"
        )
        self._assert(resp, expected_response_type="others")

    def test_binary_empty_content_is_valid_bytes(self):
        """binary response with b'' content → still bytes, passes the isinstance check."""
        resp = _make_raw_response(content_type="application/octet-stream", body=b"")
        resp.content = b""
        # Empty bytes is still bytes — assertion only checks Content-Type and isinstance
        self._assert(resp, expected_response_type="binary")

    # 🚫 REJECTION ────────────────────────────────────────────────────────

    def test_none_response_fails(self):
        """response=None → AssertionError immediately."""
        with pytest.raises(AssertionError, match="no response"):
            self.mo_assert_api_response(api_url_name=self.API_URL_NAME, response=None)

    @pytest.mark.parametrize("invalid_type", ["JSON", "PLAIN", "Html", "xml", "file"])
    def test_invalid_expected_response_type_raises(self, invalid_type):
        """Values not in the Literal set → TypeCheckError raised by typeguard."""
        from typeguard import TypeCheckError

        with pytest.raises(TypeCheckError):
            self.mo_assert_api_response(
                api_url_name=self.API_URL_NAME,
                response=_make_raw_response(),
                expected_response_type=invalid_type,
            )


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
