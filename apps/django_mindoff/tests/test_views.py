"""
Unit tests for views.py private helpers and view-method branches.

Queue-integration paths (Redis, worker process) are covered in
test_api_kit_queue_mode.py. This file targets:
  - All private helper functions (pure and near-pure)
  - View-method branches that queue tests do not reach (empty response,
    compressed payloads, rate-limit early exits, list-view filtering/pagination)
"""

import base64
import gzip
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from model_bakery import baker

from apps.django_mindoff.components.tdd_kit import MindoffTestCase
from apps.django_mindoff.components.validation_kit import MindoffValidationError
from apps.django_mindoff.models import MOQueue
from apps.django_mindoff.views import (
    SSEEventStreamRenderer,
    MindoffQueueCancelView,
    MindoffQueueDetailView,
    MindoffQueueListView,
    MindoffQueueRetryView,
    MindoffQueueStatusStreamView,
    _check_ownership,
    _get_live_progress,
    _get_queue_response,
    _is_queue_mode_api,
    _maybe_decode_compressed_json,
    _normalize_completed_response,
    _response_code_to_http_status,
    _safe_int,
    _serialize_queue_obj,
    _state_status,
    _status_payload,
    _stream_state_payload,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers: SSEEventStreamRenderer
# ---------------------------------------------------------------------------


class TestSSEEventStreamRenderer(MindoffTestCase):
    def test_render_returns_data_unchanged(self):
        renderer = SSEEventStreamRenderer()
        data = b"data: hello\n\n"
        assert renderer.render(data) == data

    def test_render_passes_none_through(self):
        renderer = SSEEventStreamRenderer()
        assert renderer.render(None) is None


# ---------------------------------------------------------------------------
# Helpers: _safe_int
# ---------------------------------------------------------------------------


class TestSafeInt(MindoffTestCase):
    @pytest.mark.parametrize(
        "raw, default, minimum, expected",
        [
            ("5", 1, 1, 5),
            ("0", 1, 1, 1),       # below minimum → clipped
            ("-10", 1, 1, 1),     # negative → clipped
            (None, 3, 1, 3),      # None → default
            ("abc", 7, 1, 7),     # non-numeric → default
            ("2", 1, 5, 5),       # below minimum → minimum
        ],
    )
    def test_safe_int(self, raw, default, minimum, expected):
        assert _safe_int(raw, default=default, minimum=minimum) == expected


# ---------------------------------------------------------------------------
# Helpers: _state_status
# ---------------------------------------------------------------------------


class TestStateStatus(MindoffTestCase):
    def test_returns_job_status_when_present(self):
        assert _state_status({"job_status": "running"}) == "running"

    def test_returns_unknown_when_key_absent(self):
        assert _state_status({}) == "unknown"

    def test_returns_unknown_when_value_is_none(self):
        assert _state_status({"job_status": None}) == "unknown"

    def test_returns_unknown_when_value_is_empty_string(self):
        assert _state_status({"job_status": ""}) == "unknown"


# ---------------------------------------------------------------------------
# Helpers: _response_code_to_http_status
# ---------------------------------------------------------------------------


class TestResponseCodeToHttpStatus(MindoffTestCase):
    def test_valid_int_returned_as_is(self):
        assert _response_code_to_http_status(200) == 200

    def test_string_int_is_coerced(self):
        assert _response_code_to_http_status("404") == 404

    def test_none_returns_default(self):
        assert _response_code_to_http_status(None) == 200
        assert _response_code_to_http_status(None, default=400) == 400

    def test_non_numeric_returns_default(self):
        assert _response_code_to_http_status("bad") == 200

    def test_out_of_range_low_returns_default(self):
        assert _response_code_to_http_status(99) == 200

    def test_out_of_range_high_returns_default(self):
        assert _response_code_to_http_status(600) == 200

    def test_boundary_values_accepted(self):
        assert _response_code_to_http_status(100) == 100
        assert _response_code_to_http_status(599) == 599


# ---------------------------------------------------------------------------
# Helpers: _normalize_completed_response
# ---------------------------------------------------------------------------


class TestNormalizeCompletedResponse(MindoffTestCase):
    def test_unwraps_legacy_envelope(self):
        inner = {"status": "ok", "data": []}
        wrapped = {"status_code": 201, "data": inner}
        result, status = _normalize_completed_response(wrapped)
        assert result == inner
        assert status == 201

    def test_plain_dict_passes_through(self):
        payload = {"status": "ok", "data": []}
        result, status = _normalize_completed_response(payload)
        assert result == payload
        assert status is None

    def test_non_dict_passes_through(self):
        result, status = _normalize_completed_response(None)
        assert result is None
        assert status is None

    def test_envelope_with_non_int_status_passes_through(self):
        wrapped = {"status_code": "200", "data": {"key": "val"}}
        result, status = _normalize_completed_response(wrapped)
        assert result == wrapped
        assert status is None

    def test_envelope_with_non_dict_inner_passes_through(self):
        wrapped = {"status_code": 200, "data": "string_not_dict"}
        result, status = _normalize_completed_response(wrapped)
        assert result == wrapped
        assert status is None


# ---------------------------------------------------------------------------
# Helpers: _is_queue_mode_api
# ---------------------------------------------------------------------------


class TestIsQueueModeApi(MindoffTestCase):
    def test_queue_mode_returns_true(self):
        api = MagicMock(process_mode="queue")
        assert _is_queue_mode_api(api) is True

    def test_direct_mode_returns_false(self):
        api = MagicMock(process_mode="direct")
        assert _is_queue_mode_api(api) is False

    def test_no_attribute_returns_false(self):
        assert _is_queue_mode_api(object()) is False


# ---------------------------------------------------------------------------
# Helpers: _maybe_decode_compressed_json
# ---------------------------------------------------------------------------


def _make_compressed_payload(data) -> dict:
    raw = json.dumps(data).encode("utf-8")
    compressed = gzip.compress(raw)
    encoded = base64.b64encode(compressed).decode("utf-8")
    return {"__compressed__": True, "codec": "gzip+base64", "data": encoded}


class TestMaybeDecodeCompressedJson(MindoffTestCase):
    def test_non_dict_passes_through(self):
        assert _maybe_decode_compressed_json("string") == "string"
        assert _maybe_decode_compressed_json(None) is None
        assert _maybe_decode_compressed_json(42) == 42

    def test_dict_without_compressed_flag_passes_through(self):
        d = {"key": "value"}
        assert _maybe_decode_compressed_json(d) == d

    def test_compressed_payload_decoded(self):
        original = {"result": [1, 2, 3]}
        assert _maybe_decode_compressed_json(_make_compressed_payload(original)) == original

    def test_missing_data_key_passes_through(self):
        payload = {"__compressed__": True, "codec": "gzip+base64"}
        assert _maybe_decode_compressed_json(payload) == payload

    def test_corrupt_data_passes_through(self):
        payload = {"__compressed__": True, "codec": "gzip+base64", "data": "!!!bad!!!"}
        assert _maybe_decode_compressed_json(payload) == payload


# ---------------------------------------------------------------------------
# Helpers: _status_payload / _stream_state_payload
# ---------------------------------------------------------------------------


class TestStatusPayload(MindoffTestCase):
    def test_full_state_mapped_correctly(self):
        state = {
            "id": "abc-123",
            "job_status": "running",
            "is_cancel": False,
            "progress": {"percent": 50, "current_step": "step1", "current_message": "msg"},
            "started_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:01:00",
        }
        result = _status_payload(state)
        assert result["id"] == "abc-123"
        assert result["job_status"] == "running"
        assert result["is_cancel"] is False
        assert result["progress"]["percent"] == 50
        assert result["progress"]["current_step"] == "step1"
        assert result["started_at"] == "2026-01-01T00:00:00"

    def test_percent_clamped_between_0_and_100(self):
        state = {"progress": {"percent": 150}, "job_status": "running"}
        assert _status_payload(state)["progress"]["percent"] == 100

        state["progress"]["percent"] = -10
        assert _status_payload(state)["progress"]["percent"] == 0

    def test_non_dict_progress_defaults_to_empty(self):
        state = {"job_status": "pending", "progress": "bad"}
        result = _status_payload(state)
        assert result["progress"]["percent"] == 0
        assert result["progress"]["current_step"] == ""

    def test_stream_state_payload_delegates_to_status_payload(self):
        state = {"job_status": "completed", "progress": {"percent": 100}}
        assert _stream_state_payload(state) == _status_payload(state)


# ---------------------------------------------------------------------------
# DB helpers (require django_db)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMOQueue(MindoffTestCase):
    def test_get_user_returns_related_user(self):
        user = User.objects.create_user(username="queue-user", password="password")
        obj = MOQueue.objects.create(
            owner_id="test-owner",
            user_ref=user,
            api_url_name="test_api",
            job_status="pending",
        )

        assert obj.get_user() == user


@pytest.mark.django_db
class TestCheckOwnership(MindoffTestCase):
    def test_no_user_ref_id_allows_any_user(self):
        obj = baker.make(MOQueue, user_ref=None, job_status="pending")
        request = MagicMock()
        request.user.id = uuid.uuid4()
        _check_ownership(request, obj)  # must not raise

    def test_matching_user_ref_allows_access(self):
        user = baker.make(User)
        obj = baker.make(MOQueue, user_ref=user, job_status="pending")
        request = MagicMock()
        request.user.id = user.id
        _check_ownership(request, obj)  # must not raise

    def test_mismatched_user_raises_permission_denied(self):
        user = baker.make(User)
        obj = baker.make(MOQueue, user_ref=user, job_status="pending")
        request = MagicMock()
        request.user.id = uuid.uuid4()  # different user
        with pytest.raises(MindoffValidationError):
            _check_ownership(request, obj)


@pytest.mark.django_db
class TestGetQueueResponse(MindoffTestCase):
    def test_none_response_returns_empty_dict(self):
        obj = baker.make(MOQueue, response=None, job_status="completed")
        assert _get_queue_response(obj) == {}

    def test_non_dict_non_empty_response_returns_empty_dict(self):
        # Use a non-empty list so it's truthy ([] → {} via `or {}` would mask the branch)
        obj = baker.make(MOQueue, response=["a", "b"], job_status="completed")
        assert _get_queue_response(obj) == {}

    def test_plain_dict_response_returned_as_is(self):
        payload = {"status": "ok", "data": {"id": 1}}
        obj = baker.make(MOQueue, response=payload, job_status="completed")
        assert _get_queue_response(obj) == payload

    def test_compressed_response_is_decoded(self):
        original = {"status": "ok", "data": {"id": 42}}
        obj = baker.make(MOQueue, response=_make_compressed_payload(original), job_status="completed")
        assert _get_queue_response(obj) == original

    def test_compressed_non_dict_result_wrapped(self):
        raw = json.dumps([1, 2, 3]).encode("utf-8")
        encoded = base64.b64encode(gzip.compress(raw)).decode("utf-8")
        payload = {"__compressed__": True, "codec": "gzip+base64", "data": encoded}
        obj = baker.make(MOQueue, response=payload, job_status="completed")
        assert _get_queue_response(obj) == {"result": [1, 2, 3]}

    def test_compressed_with_missing_data_key_returns_empty(self):
        payload = {"__compressed__": True, "codec": "gzip+base64"}
        obj = baker.make(MOQueue, response=payload, job_status="completed")
        assert _get_queue_response(obj) == {}

    def test_compressed_with_corrupt_data_returns_empty(self):
        payload = {"__compressed__": True, "codec": "gzip+base64", "data": "!!!bad!!!"}
        obj = baker.make(MOQueue, response=payload, job_status="completed")
        assert _get_queue_response(obj) == {}


@pytest.mark.django_db
class TestSerializeQueueObj(MindoffTestCase):
    def test_all_fields_present_in_result(self):
        obj = baker.make(MOQueue, job_status="pending", api_url_name="test_api")
        result = _serialize_queue_obj(obj)
        assert "job_status" in result
        assert "api_url_name" in result
        assert result["job_status"] == "pending"

    def test_datetime_fields_are_iso_strings(self):
        obj = baker.make(MOQueue, job_status="pending")
        result = _serialize_queue_obj(obj)
        assert isinstance(result["created_at"], str)
        assert "T" in result["created_at"]

    def test_compressed_json_field_is_decoded(self):
        original = {"key": "value"}
        obj = baker.make(MOQueue, response=_make_compressed_payload(original), job_status="completed")
        assert _serialize_queue_obj(obj)["response"] == original


# ---------------------------------------------------------------------------
# _get_live_progress (mocked Redis)
# ---------------------------------------------------------------------------


class TestGetLiveProgress(MindoffTestCase):
    @patch("apps.django_mindoff.views.get_queue_status")
    def test_known_state_returns_progress(self, mock_status):
        mock_status.return_value = {
            "job_status": "running",
            "progress": {"percent": 60, "current_step": "s1", "current_message": "m1"},
        }
        result = _get_live_progress("some-uuid")
        assert result["percent"] == 60
        assert result["current_step"] == "s1"

    @patch("apps.django_mindoff.views.rehydrate_queue_state")
    @patch("apps.django_mindoff.views.get_queue_status")
    def test_unknown_then_rehydrate_returns_progress(self, mock_status, mock_rehydrate):
        mock_status.return_value = {"job_status": None}
        mock_rehydrate.return_value = {
            "job_status": "completed",
            "progress": {"percent": 100, "current_step": "", "current_message": ""},
        }
        result = _get_live_progress("some-uuid")
        assert result["percent"] == 100

    @patch("apps.django_mindoff.views.rehydrate_queue_state")
    @patch("apps.django_mindoff.views.get_queue_status")
    def test_unknown_after_rehydrate_returns_empty(self, mock_status, mock_rehydrate):
        mock_status.return_value = {"job_status": None}
        mock_rehydrate.return_value = {"job_status": None}
        assert _get_live_progress("some-uuid") == {}

    @patch("apps.django_mindoff.views.get_queue_status")
    def test_missing_progress_returns_zeroed_dict(self, mock_status):
        mock_status.return_value = {"job_status": "running"}
        result = _get_live_progress("some-uuid")
        assert result == {"percent": 0, "current_step": "", "current_message": ""}


# ---------------------------------------------------------------------------
# MindoffQueueDetailView — response-handler branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueueDetailViewResponseHandlers(MindoffTestCase):
    def _view(self):
        return MindoffQueueDetailView()

    def test_cancelled_response_shape(self):
        obj = baker.make(MOQueue, job_status="cancelled")
        result = self._view()._cancelled_response(obj, str(obj.id))
        assert result.data["message"]["code"] == "QUEUE_TASK_CANCELLED"

    def test_pending_response_shape(self):
        obj = baker.make(MOQueue, job_status="pending")
        result = self._view()._pending_response(obj, str(obj.id))
        assert result.data["message"]["code"] == "QUEUE_TASK_PENDING"

    def test_unknown_response_shape(self):
        obj = baker.make(MOQueue, job_status="whatever")
        result = self._view()._unknown_response(obj, str(obj.id))
        assert result.data["message"]["code"] == "QUEUE_TASK_UNKNOWN_STATUS"

    def test_running_response_shape(self):
        obj = baker.make(MOQueue, job_status="running")
        with patch("apps.django_mindoff.views._get_live_progress", return_value={}):
            result = self._view()._running_response(obj, str(obj.id))
        assert result.data["message"]["code"] == "QUEUE_TASK_RUNNING"
        assert "progress" in result.data["data"]

    def test_failed_response_with_no_payload(self):
        obj = baker.make(MOQueue, response=None, job_status="failed", error={"msg": "err"})
        result = self._view()._failed_response(obj, str(obj.id))
        assert result.data["message"]["code"] == "QUEUE_TASK_FAILED"

    def test_completed_response_with_valid_payload(self):
        payload = {"status": "ok", "message": {"code": "SUCCESS"}, "data": {}}
        obj = baker.make(MOQueue, response=payload, job_status="completed", response_code=200)
        result = self._view()._completed_response(obj, str(obj.id))
        assert result.status_code == 200

    def test_completed_response_with_wrapped_envelope(self):
        inner = {"status": "ok", "message": {"code": "SUCCESS"}, "data": {}}
        wrapped = {"status_code": 201, "data": inner}
        obj = baker.make(MOQueue, response=wrapped, job_status="completed")
        result = self._view()._completed_response(obj, str(obj.id))
        assert result.status_code == 201

    def test_completed_response_empty_payload_returns_unknown_status(self):
        obj = baker.make(MOQueue, response=None, job_status="completed")
        result = self._view()._completed_response(obj, str(obj.id))
        assert result.data["message"]["code"] == "QUEUE_TASK_UNKNOWN_STATUS"

    def test_response_for_status_dispatches_each_status(self):
        for status in ("cancelled", "pending", "failed", "running", "completed"):
            obj = baker.make(MOQueue, job_status=status, response=None)
            with patch.object(
                MindoffQueueDetailView, f"_{status}_response", return_value=MagicMock()
            ) as mock_handler:
                self._view()._response_for_status(obj, str(obj.id))
                mock_handler.assert_called_once()

    def test_response_for_status_unknown_falls_back(self):
        obj = baker.make(MOQueue, job_status="mystery_status")
        with patch.object(
            MindoffQueueDetailView, "_unknown_response", return_value=MagicMock()
        ) as mock_handler:
            self._view()._response_for_status(obj, str(obj.id))
            mock_handler.assert_called_once()


# ---------------------------------------------------------------------------
# MindoffQueueDetailView — _apply_rate_limit branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueueDetailRateLimitBranches(MindoffTestCase):
    def _obj(self):
        return baker.make(MOQueue, job_status="pending", api_url_name="test_api")

    def test_non_queue_mode_api_skips(self):
        obj = self._obj()
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: MagicMock(process_mode="direct")
            MindoffQueueDetailView()._apply_rate_limit(MagicMock(), obj, obj.id)

    def test_queue_mode_without_limit_skips(self):
        obj = self._obj()
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: MagicMock(
                process_mode="queue", api_url_name="x", queue_detail_api_limit=None
            )
            MindoffQueueDetailView()._apply_rate_limit(MagicMock(), obj, obj.id)

    def test_queue_mode_with_limit_not_exceeded(self):
        obj = self._obj()
        with (
            patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get,
            patch("apps.django_mindoff.views.is_ratelimited", return_value=False),
        ):
            mock_get.return_value = lambda: MagicMock(
                process_mode="queue", api_url_name="x", queue_detail_api_limit="10/m"
            )
            MindoffQueueDetailView()._apply_rate_limit(MagicMock(), obj, obj.id)


# ---------------------------------------------------------------------------
# MindoffQueueListView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueueListView(MindoffTestCase):
    def _run(self, query_params=None):
        request = MagicMock()
        request.GET = query_params or {}
        return MindoffQueueListView().run(request)

    def test_empty_queue_returns_success(self):
        MOQueue.objects.all().delete()
        result = self._run()
        assert result.data["message"]["code"] == "SUCCESS"
        assert result.data["data"]["tasks"] == []
        assert result.data["data"]["count"] == 0

    def test_filters_by_task_id(self):
        obj = baker.make(MOQueue, job_status="pending")
        baker.make(MOQueue, job_status="pending", _quantity=2)
        result = self._run({"id": str(obj.id)})
        assert result.data["data"]["count"] == 1

    def test_filters_by_job_status(self):
        baker.make(MOQueue, job_status="pending", _quantity=2)
        baker.make(MOQueue, job_status="completed", _quantity=3)
        result = self._run({"job_status": "pending"})
        assert result.data["data"]["count"] == 2

    def test_filters_by_user_ref_id(self):
        user = baker.make(User)
        baker.make(MOQueue, user_ref=user, job_status="pending", _quantity=2)
        baker.make(MOQueue, user_ref=None, job_status="pending", _quantity=3)
        result = self._run({"user_ref_id": str(user.id)})
        assert result.data["data"]["count"] == 2

    def test_filters_by_owner_id(self):
        baker.make(MOQueue, job_status="pending", owner_id="owner-A", _quantity=2)
        baker.make(MOQueue, job_status="pending", owner_id="owner-B", _quantity=1)
        result = self._run({"owner_id": "owner-A"})
        assert result.data["data"]["count"] == 2

    def test_filters_by_api_url_name(self):
        baker.make(MOQueue, job_status="pending", api_url_name="api_foo", _quantity=2)
        baker.make(MOQueue, job_status="pending", api_url_name="api_bar", _quantity=1)
        result = self._run({"api_url_name": "api_foo"})
        assert result.data["data"]["count"] == 2

    def test_pagination_page_size_respected(self):
        baker.make(MOQueue, job_status="pending", _quantity=10)
        result = self._run({"page_size": "3"})
        assert len(result.data["data"]["tasks"]) == 3

    def test_page_size_capped_at_200(self):
        baker.make(MOQueue, job_status="pending", _quantity=5)
        result = self._run({"page_size": "999"})
        assert result.data["data"]["page_size"] == 200

    def test_out_of_range_page_falls_back_to_last(self):
        baker.make(MOQueue, job_status="pending", _quantity=3)
        result = self._run({"page": "999", "page_size": "2"})
        assert result.data["message"]["code"] == "SUCCESS"

    def test_initial_rate_limit_reads_from_settings(self):
        from django.conf import settings
        view = MindoffQueueListView()
        with patch.object(
            MindoffQueueListView.__bases__[0],
            "_initial_validate_api_rate_limit",
        ):
            view._initial_validate_api_rate_limit(MagicMock())
        expected = getattr(settings, "MINDOFF_QUEUE_LIST_API_REQUEST_LIMIT", "120/m")
        assert view.api_request_limit == expected


# ---------------------------------------------------------------------------
# MindoffQueueStatusStreamView — SSE helper branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSSEStreamHelpers(MindoffTestCase):
    def _view(self):
        return MindoffQueueStatusStreamView()

    def test_fallback_state_shape(self):
        obj = baker.make(MOQueue, job_status="failed", error={"msg": "oops"})
        state = self._view()._fallback_state(obj)
        assert state["id"] == str(obj.id)
        assert state["job_status"] == "failed"
        assert state["is_cancel"] is False
        assert state["progress"]["percent"] == 0

    def test_fallback_state_cancelled_is_cancel_true(self):
        obj = baker.make(MOQueue, job_status="cancelled")
        state = self._view()._fallback_state(obj)
        assert state["is_cancel"] is True

    def test_fallback_state_completed_progress_100(self):
        obj = baker.make(MOQueue, job_status="completed")
        assert self._view()._fallback_state(obj)["progress"]["percent"] == 100

    def test_maybe_sse_event_returns_none_when_state_unchanged(self):
        state = {"job_status": "running", "progress": {"percent": 50}}
        last = _stream_state_payload(state)
        assert self._view()._maybe_sse_event(state, last) is None

    def test_maybe_sse_event_returns_event_when_state_changed(self):
        state = {"job_status": "running", "progress": {"percent": 50}}
        assert self._view()._maybe_sse_event(state, None) is not None

    def test_acquire_sse_limit_skips_for_direct_mode_api(self):
        obj = baker.make(MOQueue, job_status="running", api_url_name="test_api")
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: MagicMock(process_mode="direct")
            self._view()._acquire_sse_limit(obj)

    def test_acquire_sse_limit_skips_when_no_max_streams(self):
        obj = baker.make(MOQueue, job_status="running", api_url_name="test_api")
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: MagicMock(
                process_mode="queue", queue_status_stream_api_limit=None
            )
            self._view()._acquire_sse_limit(obj)


# ---------------------------------------------------------------------------
# MindoffQueueCancelView / MindoffQueueRetryView — rate-limit branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCancelRetryRateLimitBranches(MindoffTestCase):
    def _obj(self):
        return baker.make(MOQueue, job_status="pending", api_url_name="test_api")

    def _queue_api(self, cancel_limit=None, retry_limit=None):
        m = MagicMock(process_mode="queue", api_url_name="x")
        m.queue_cancel_api_limit = cancel_limit
        m.queue_retry_api_limit = retry_limit
        return m

    def test_cancel_rate_limit_skips_for_direct_mode(self):
        obj = self._obj()
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: MagicMock(process_mode="direct")
            MindoffQueueCancelView()._apply_cancel_rate_limit(MagicMock(), obj, obj.id)

    def test_cancel_rate_limit_skips_when_no_limit(self):
        obj = self._obj()
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: self._queue_api(cancel_limit=None)
            MindoffQueueCancelView()._apply_cancel_rate_limit(MagicMock(), obj, obj.id)

    def test_cancel_rate_limit_not_exceeded(self):
        obj = self._obj()
        with (
            patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get,
            patch("apps.django_mindoff.views.is_ratelimited", return_value=False),
        ):
            mock_get.return_value = lambda: self._queue_api(cancel_limit="10/m")
            MindoffQueueCancelView()._apply_cancel_rate_limit(MagicMock(), obj, obj.id)

    def test_retry_rate_limit_skips_for_direct_mode(self):
        obj = self._obj()
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: MagicMock(process_mode="direct")
            MindoffQueueRetryView()._apply_retry_rate_limit(MagicMock(), obj, obj.id)

    def test_retry_rate_limit_skips_when_no_limit(self):
        obj = self._obj()
        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_get.return_value = lambda: self._queue_api(retry_limit=None)
            MindoffQueueRetryView()._apply_retry_rate_limit(MagicMock(), obj, obj.id)

    def test_retry_rate_limit_not_exceeded(self):
        obj = self._obj()
        with (
            patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get,
            patch("apps.django_mindoff.views.is_ratelimited", return_value=False),
        ):
            mock_get.return_value = lambda: self._queue_api(retry_limit="10/m")
            MindoffQueueRetryView()._apply_retry_rate_limit(MagicMock(), obj, obj.id)
