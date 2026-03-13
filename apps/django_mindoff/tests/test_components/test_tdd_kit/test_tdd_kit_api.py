from unittest.mock import MagicMock, patch
import pytest
from ....components.tdd_kit import MindoffTestCase, MindoffRouterTestCase


@pytest.mark.django_db
class TestMoCallApi(MindoffTestCase):

    API_URL_NAME = "tdd_test__sample_api"

    def _patched_call(self, api_cls, http_method, **call_kwargs):
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=api_cls,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(
                self.client, http_method, return_value=_make_raw_response()
            ) as mock,
        ):
            response = self.mo_mock_call_api(self.API_URL_NAME, **call_kwargs)
        return response, mock

    def test_returns_raw_response_object(self):
        """ACCEPTANCE: Validates returns raw response object."""
        response, _ = self._patched_call(_make_api_cls(method="get"), "get")
        assert hasattr(response, "status_code")
        assert hasattr(response, "headers")
        assert hasattr(response, "content")

    @pytest.mark.parametrize("method", ["get", "delete"])
    def test_get_and_delete_send_no_payload(self, method):
        """ACCEPTANCE: Validates get and delete send no payload."""
        _, mock = self._patched_call(_make_api_cls(method=method), method)
        assert "data" not in (mock.call_args.kwargs or {})

    @pytest.mark.parametrize("method", ["post", "put", "patch"])
    def test_mutation_methods_send_provided_payload(self, method):
        """ACCEPTANCE: Validates mutation methods send provided payload."""
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
        """BOUNDARY: Validates mutation methods with no payload send empty dict."""
        _, mock = self._patched_call(_make_api_cls(method=method), method)
        sent = (
            mock.call_args.kwargs["data"]
            if "data" in mock.call_args.kwargs
            else mock.call_args.args[1]
        )
        assert sent == {}

    def test_url_kwargs_passed_to_reverse(self):
        """ACCEPTANCE: Validates url kwargs passed to reverse."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ) as mock_rev,
            patch.object(self.client, "get", return_value=_make_raw_response()),
        ):
            self.mo_mock_call_api(self.API_URL_NAME, url_kwargs={"pk": 42})
        mock_rev.assert_called_once_with(self.API_URL_NAME, kwargs={"pk": 42})

    def test_no_url_kwargs_calls_reverse_with_empty_dict(self):
        """BOUNDARY: Validates no url kwargs calls reverse with empty dict."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ) as mock_rev,
            patch.object(self.client, "get", return_value=_make_raw_response()),
        ):
            self.mo_mock_call_api(self.API_URL_NAME)
        mock_rev.assert_called_once_with(self.API_URL_NAME, kwargs={})

    def test_query_params_appended_to_url(self):
        """ACCEPTANCE: Validates query params appended to url."""
        _, mock = self._patched_call(
            _make_api_cls(method="get"), "get", query_params={"page": 2, "search": "hi"}
        )
        url = mock.call_args.args[0]
        assert "page=2" in url
        assert "search=hi" in url

    def test_no_query_params_omits_query_string(self):
        """ACCEPTANCE: Validates no query params omits query string."""
        _, mock = self._patched_call(_make_api_cls(method="get"), "get")
        url = mock.call_args.args[0]
        assert "?" not in url

    def test_user_triggers_force_authenticate(self):
        """ACCEPTANCE: Validates user triggers force authenticate."""
        fake_user = MagicMock()
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", return_value=_make_raw_response()),
            patch.object(self.client, "force_authenticate") as mock_auth,
        ):
            self.mo_mock_call_api(self.API_URL_NAME, user=fake_user)
        mock_auth.assert_called_once_with(user=fake_user)

    def test_no_user_skips_force_authenticate(self):
        """ACCEPTANCE: Validates no user skips force authenticate."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", return_value=_make_raw_response()),
            patch.object(self.client, "force_authenticate") as mock_auth,
        ):
            self.mo_mock_call_api(self.API_URL_NAME)
        mock_auth.assert_not_called()

    def test_accept_header_always_json_and_custom_headers_merged(self):
        """ACCEPTANCE: Validates accept header always json and custom headers merged."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", return_value=_make_raw_response()) as mock,
        ):
            self.mo_mock_call_api(self.API_URL_NAME, headers={"X-Custom": "value"})
        sent = mock.call_args.kwargs.get("headers", {})
        assert sent.get("Accept") == "application/json"
        assert sent.get("X-Custom") == "value"

    def test_mutation_method_content_type_auto_set(self):
        """ACCEPTANCE: Validates mutation method content type auto set."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="post"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(
                self.client, "post", return_value=_make_raw_response()
            ) as mock,
        ):
            self.mo_mock_call_api(self.API_URL_NAME, payload={"x": 1})
        sent = mock.call_args.kwargs.get("headers", {})
        assert sent.get("Content-Type") == "application/json"

    def test_callback_without_view_class_or_version_map_raises_with_message(self):
        """REJECTION: Validates callback without view class or version map raises with message."""
        from django.core.exceptions import ImproperlyConfigured

        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                side_effect=ImproperlyConfigured("no view_class or VERSION_MAP"),
            ),
        ):
            with pytest.raises(
                ImproperlyConfigured, match="no view_class or VERSION_MAP"
            ):
                self.mo_mock_call_api(self.API_URL_NAME)

    def test_version_not_in_version_map_raises_key_error(self):
        """REJECTION: Validates version not in version map raises key error."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=True,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                side_effect=KeyError("Version 99 not registered"),
            ),
        ):
            with pytest.raises(KeyError):
                self.mo_mock_call_api(self.API_URL_NAME, url_kwargs={"version": 99})

    def test_content_type_not_overwritten_when_caller_provides_it(self):
        """REJECTION: Validates content type not overwritten when caller provides it."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="post"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(
                self.client, "post", return_value=_make_raw_response()
            ) as mock,
        ):
            self.mo_mock_call_api(
                self.API_URL_NAME,
                payload={"x": 1},
                headers={"Content-Type": "multipart/form-data"},
            )
        sent = mock.call_args.kwargs.get("headers", {})
        assert sent.get("Content-Type") == "multipart/form-data"

    def test_queue_mode_api_returns_direct_response(self):
        """ACCEPTANCE: Validates queue mode api returns direct response."""
        direct_response = _make_raw_response(
            body={"message": {"code": "SUCCESS"}, "data": {"ok": 1}}
        )
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get", process_mode="queue"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse",
                return_value="/enqueue/",
            ),
            patch.object(self.client, "get", return_value=direct_response) as mock_get,
        ):
            response = self.mo_mock_call_api(self.API_URL_NAME)

        assert response is direct_response
        assert mock_get.call_count == 1

    def test_queue_mode_api_sets_and_clears_force_direct_flag(self):
        """ACCEPTANCE: Validates queue mode api sets and clears force direct flag."""
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
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse",
                return_value="/enqueue/",
            ),
            patch.object(self.client, "get", side_effect=_capture_flag),
        ):
            self.mo_mock_call_api(self.API_URL_NAME)

        assert observed_during == [True]
        assert getattr(_test_force_direct, "active", False) is False

    def test_extra_kwargs_forwarded_to_client(self):
        """ACCEPTANCE: Validates extra kwargs forwarded to client."""
        _, mock = self._patched_call(
            _make_api_cls(method="get"), "get", REMOTE_ADDR="1.2.3.4"
        )
        assert mock.call_count == 1

    def test_force_direct_reset_even_when_client_raises(self):
        """REJECTION: Validates force direct reset even when client raises."""
        from apps.django_mindoff.components.api_kit import _test_force_direct

        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError):
                self.mo_mock_call_api(self.API_URL_NAME)

        assert getattr(_test_force_direct, "active", False) is False

    def test_versioned_url_happy_path(self):
        """ACCEPTANCE: Validates versioned url happy path."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=True,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method="get"),
            ) as mock_attrs,
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
            patch.object(self.client, "get", return_value=_make_raw_response()),
        ):
            self.mo_mock_call_api(self.API_URL_NAME, url_kwargs={"version": 1})
        mock_attrs.assert_called_once_with(self.API_URL_NAME, version=1)

    @pytest.mark.parametrize(
        "method, payload",
        [
            ("get", {"bad": "payload"}),
            ("delete", {"bad": "payload"}),
        ],
    )
    def test_get_and_delete_with_payload_raises(self, method, payload):
        """REJECTION: Validates get and delete with payload raises."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                return_value=_make_api_cls(method=method),
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit.reverse", return_value="/fake/"
            ),
        ):
            with pytest.raises(Exception):
                self.mo_mock_call_api(self.API_URL_NAME, payload=payload)

    def test_versioned_url_missing_version_in_kwargs_raises(self):
        """REJECTION: Validates versioned url missing version in kwargs raises."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=True,
            ),
        ):
            with pytest.raises(ValueError, match="version"):
                self.mo_mock_call_api(self.API_URL_NAME)

    def test_unknown_url_name_raises_lookup_error(self):
        """REJECTION: Validates unknown url name raises lookup error."""
        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                side_effect=LookupError("No URL found"),
            ),
        ):
            with pytest.raises(LookupError):
                self.mo_mock_call_api("nonexistent_url_name")

    def test_empty_version_map_raises_improperly_configured(self):
        """REJECTION: Validates empty version map raises improperly configured."""
        from django.core.exceptions import ImproperlyConfigured

        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=True,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                side_effect=ImproperlyConfigured("empty VERSION_MAP"),
            ),
        ):
            with pytest.raises(ImproperlyConfigured):
                self.mo_mock_call_api(self.API_URL_NAME, url_kwargs={"version": 1})


@pytest.mark.django_db
class TestMoAssertApiResponse(MindoffTestCase):

    API_URL_NAME = "tdd_test__assert_api"

    def _assert(self, response, **kwargs):
        self.mo_assert_api_response(
            api_url_name=self.API_URL_NAME,
            response=response,
            **kwargs,
        )

    def test_correct_status_code_passes(self):
        """ACCEPTANCE: Validates correct status code passes."""
        self._assert(_make_raw_response())

    def test_wrong_status_code_fails(self):
        """REJECTION: Validates wrong status code fails."""
        resp = _make_raw_response()
        resp.status_code = 404
        with pytest.raises(AssertionError, match="404"):
            self._assert(resp)

    def test_custom_expected_status_code_passes(self):
        """ACCEPTANCE: Validates custom expected status code passes."""
        resp = _make_raw_response(content_type="text/plain", body="bad request")
        resp.status_code = 400
        self._assert(resp, expected_status_code=400, expected_response_type="plain")

    def test_custom_expected_status_code_mismatch_fails(self):
        """REJECTION: Validates custom expected status code mismatch fails."""
        resp = _make_raw_response()
        with pytest.raises(AssertionError, match="200"):
            self._assert(resp, expected_status_code=201)

    def test_json_content_type_passes(self):
        """ACCEPTANCE: Validates json content type passes."""
        self._assert(_make_raw_response(content_type="application/json"))

    def test_wrong_content_type_for_json_fails(self):
        """REJECTION: Validates wrong content type for json fails."""
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
        """ACCEPTANCE: Validates valid response types pass."""
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
        """REJECTION: Validates mismatched content type fails."""
        resp = _make_raw_response(content_type=content_type, body=body)
        with pytest.raises(AssertionError, match=match):
            self._assert(resp, expected_response_type=response_type)

    def test_others_response_type_skips_content_type_assertion(self):
        """ACCEPTANCE: Validates others response type skips content type assertion."""
        resp = _make_raw_response(
            content_type="application/x-custom-format", body=b"\xde\xad\xbe\xef"
        )
        self._assert(resp, expected_response_type="others")

    def test_binary_empty_content_is_valid_bytes(self):
        """BOUNDARY: Validates binary empty content is valid bytes."""
        resp = _make_raw_response(content_type="application/octet-stream", body=b"")
        resp.content = b""
        self._assert(resp, expected_response_type="binary")

    def test_binary_empty_content_type_fails(self):
        """REJECTION: Validates binary empty content type fails."""
        resp = _make_raw_response(
            content_type="application/octet-stream", body=b"\xde\xad"
        )
        resp.headers = {"Content-Type": ""}
        with pytest.raises(AssertionError):
            self._assert(resp, expected_response_type="binary")

    def test_binary_with_json_content_type_fails(self):
        """REJECTION: Validates binary with json content type fails."""
        resp = _make_raw_response(content_type="application/json", body=b"\xff\xfe")
        with pytest.raises(AssertionError):
            self._assert(resp, expected_response_type="binary")

    def test_binary_non_bytes_content_fails(self):
        """REJECTION: Validates binary non bytes content fails."""
        resp = _make_raw_response(content_type="application/octet-stream", body=b"ok")
        resp.content = "not-bytes"
        with pytest.raises(AssertionError):
            self._assert(resp, expected_response_type="binary")

    def test_others_with_non_200_status_passes_when_expected(self):
        """ACCEPTANCE: Validates others with non 200 status passes when expected."""
        resp = _make_raw_response(content_type="application/x-custom", body=b"\x01")
        resp.status_code = 202
        self._assert(resp, expected_response_type="others", expected_status_code=202)

    def test_none_response_fails(self):
        """REJECTION: Validates none response fails."""
        with pytest.raises(AssertionError, match="no response"):
            self.mo_assert_api_response(api_url_name=self.API_URL_NAME, response=None)

    @pytest.mark.parametrize("invalid_type", ["JSON", "PLAIN", "Html", "xml", "file"])
    def test_invalid_expected_response_type_raises(self, invalid_type):
        """REJECTION: Validates invalid expected response type raises."""
        from typeguard import TypeCheckError

        with pytest.raises(TypeCheckError):
            self.mo_assert_api_response(
                api_url_name=self.API_URL_NAME,
                response=_make_raw_response(),
                expected_response_type=invalid_type,
            )


@pytest.mark.django_db
class TestMoCreateUser(MindoffTestCase):

    def test_creates_user_with_auto_username(self):
        """ACCEPTANCE: Validates creates user with auto username."""
        from django.contrib.auth import get_user_model

        user = self.mo_mock_user()
        assert user.pk is not None
        assert get_user_model().objects.filter(pk=user.pk).exists()

    def test_creates_user_with_explicit_username(self):
        """ACCEPTANCE: Validates creates user with explicit username."""
        user = self.mo_mock_user(username="alice")
        assert user.username == "alice"

    def test_password_is_set_and_usable(self):
        """ACCEPTANCE: Validates password is set and usable."""
        code = "s3cur3!"
        user = self.mo_mock_user(username="bob", password=code)
        assert user.check_password(code)

    def test_duplicate_username_returns_existing_user(self):
        """REJECTION: Validates duplicate username returns existing user."""
        user1 = self.mo_mock_user(username="carol")
        user2 = self.mo_mock_user(username="carol")
        assert user1.pk == user2.pk

    def test_extra_fields_are_applied(self):
        """ACCEPTANCE: Validates extra fields are applied."""
        user = self.mo_mock_user(username="dave", email="dave@example.com")
        assert user.email == "dave@example.com"

    def test_none_password_skips_set_password(self):
        """BOUNDARY: Validates none password skips set password."""
        user = self.mo_mock_user(username="nopw", password=None)
        assert user.pk is not None


@pytest.mark.django_db
class TestMindoffRouterTestCase(MindoffTestCase):

    def _make_router_case(self, version_map: dict):
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

    def test_every_version_dispatches_to_correct_class(self):
        """ACCEPTANCE: Validates every version dispatches to correct class."""

        class V1View:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

        class V2View:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

        case = self._make_router_case({1: V1View, 2: V2View})
        case.test_every_version_dispatches_to_correct_class()

    def test_unknown_version_returns_404_with_correct_body(self):
        """REJECTION: Validates unknown version returns 404 with correct body."""

        class V1View:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

        case = self._make_router_case({1: V1View})
        case.test_unknown_version_returns_404_with_correct_body()

    def test_wrong_version_dispatches_to_wrong_class_fails(self):
        """REJECTION: Validates wrong version dispatches to wrong class fails."""

        class RightView:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

        class WrongView:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

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

    def test_404_available_versions_mismatch_fails(self):
        """REJECTION: Validates 404 available versions mismatch fails."""
        from rest_framework.response import Response

        class V1View:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

        class V2View:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

        class BadRouter:
            VERSION_MAP = {1: V1View, 2: V2View}

            def __call__(self, request, version):
                return Response(
                    {
                        "message": {"code": "INVALID_API_VERSION"},
                        "data": {"available_versions": [1]},
                    },
                    status=404,
                )

        class ConcreteRouterCase(MindoffRouterTestCase):
            app_module = "__fake__"
            router_function_name = "bad_router"

        instance = ConcreteRouterCase()
        instance.router = BadRouter()
        instance.version_map = {1: V1View, 2: V2View}

        with pytest.raises(AssertionError):
            instance.test_unknown_version_returns_404_with_correct_body()

    def test_correct_404_body_missing_code_fails(self):
        """REJECTION: Validates correct 404 body missing code fails."""
        from rest_framework.response import Response

        class V1View:
            @classmethod
            def as_view(cls):
                return lambda request, **kwargs: None

        class BadRouter:
            VERSION_MAP = {1: V1View}

            def __call__(self, request, version):
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
