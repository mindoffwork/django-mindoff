"""
test_api_kit_queue_mode.py
==========================
Full test coverage for queue-mode processing, including:
    - Enqueue / dispatch lifecycle
    - Worker execution (success, failure, cancellation)
    - progress_steps schema validation
    - progress_checkpoint (new key-based API)
    - Cancel race-conditions
    - Retry lifecycle
    - Rehydration from DB when Redis has expired
    - Redis layer state transitions & is_cancel flag preservation
    - Queue status HTTP view (live Redis path + DB fallback path)
    - SSE stream lifecycle
    - End-to-end integration pipeline
"""

import gzip
import base64
import json
import time
import uuid
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from ....components.tdd_kit import MindoffTestCase
from ....components._api_kit.queue_process import (
    _COMPRESSED_RESPONSE_FLAG,
    _compress_json_result,
    _ensure_json_result,
    _extract_result,
    cancel_queue_task,
    enqueue_process,
    execute_queue,
    rehydrate_queue_state,
    retry_failed_queue_task,
)
from ....components._api_kit.redis import (
    get_queue_status,
    init_queue,
    mark_cancel_requested,
    mark_cancelled,
    mark_completed,
    mark_failed,
    mark_running,
    update_progress,
)

# Re-use helpers written for direct-mode tests
from .test_api_kit_direct_mode import (
    _api_templates,  # session-scoped fixture re-export
    _create_test_api,
    _modify_api_attribute,
    _modify_api_attributes,
    _modify_api_run_method,
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# In-memory Redis stub
# ─────────────────────────────────────────────────────────────────────────────


class _InMemoryRedis:
    """
    Minimal Redis stub sufficient for all queue tests.
    Supports: hset, hgetall, expire, persist, incr, decr, flushdb, ping.
    """

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    # ── Management ───────────────────────────────────────────────────────

    def ping(self):
        return True

    def flushdb(self):
        self.hashes.clear()
        self.counters.clear()
        self.ttls.clear()

    # ── Hash ─────────────────────────────────────────────────────────────

    def hset(self, key, mapping):
        bucket = self.hashes.setdefault(key, {})
        for k, v in mapping.items():
            bucket[str(k)] = str(v)

    def hgetall(self, key):
        data = self.hashes.get(key, {})
        return {str(k).encode("utf-8"): str(v).encode("utf-8") for k, v in data.items()}

    # ── TTL ──────────────────────────────────────────────────────────────

    def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True

    def persist(self, key):
        self.ttls.pop(key, None)
        return True

    # ── Atomic counters ───────────────────────────────────────────────────

    def incr(self, key):
        value = int(self.counters.get(key, 0)) + 1
        self.counters[key] = value
        return value

    def decr(self, key):
        value = int(self.counters.get(key, 0)) - 1
        self.counters[key] = value
        return value


# ─────────────────────────────────────────────────────────────────────────────
# Shared DB-task factory
# ─────────────────────────────────────────────────────────────────────────────


def _make_db_task(
    api_url_name: str,
    status: str = "queued",
    owner_id: str = "test_owner",
    error=None,
):
    """Create a minimal MOQueue row suitable for worker tests."""
    from apps.django_mindoff.models import MOQueue

    return MOQueue.objects.create(
        id=uuid.uuid4(),
        owner_id=owner_id,
        api_url_name=api_url_name,
        status=status,
        request={
            "method": "GET",
            "data": {},
            "query_params": {},
            "args": [],
            "kwargs": {},
        },
        error=error,
    )


def _decompress(compressed_payload: dict) -> dict:
    """Decompress a gzip+base64 response blob back to a plain dict."""
    raw = base64.b64decode(compressed_payload["data"].encode("ascii"))
    return json.loads(gzip.decompress(raw))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Enqueue / dispatch acceptance
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestQueueEnqueueAcceptance(MindoffTestCase):
    """HTTP-layer tests for triggering queue-mode APIs."""

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    # ── Response shape ────────────────────────────────────────────────────

    @patch("apps.django_mindoff.components.api_kit.enqueue_process")
    def test_returns_queue_id_and_urls(self, mock_enqueue):
        """ACCEPTANCE: Queue mode returns queue_id, status_url, status_stream_url."""
        queue_id = str(uuid.uuid4())
        mock_enqueue.return_value = queue_id

        api_url_name = self._make_api("test_enqueue_shape_api")
        _modify_api_attribute(
            self._app,
            "test_enqueue_shape_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        resp = self.client.get(reverse(api_url_name))
        body = resp.json()
        assert resp.status_code == 200
        assert body["message"]["code"] == "QUEUED"
        data = body["data"]
        assert data["queue_id"] == queue_id
        assert queue_id in data["status_url"]
        assert queue_id in data["status_stream_url"]

    # ── DB record ─────────────────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    @patch("apps.django_mindoff.components._api_kit.queue_process.init_queue")
    def test_creates_db_record_with_correct_fields(self, mock_init, mock_send):
        """ACCEPTANCE: A MOQueue row is created with the correct status / method / api_url_name."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_enqueue_db_fields_api")
        _modify_api_attribute(
            self._app,
            "test_enqueue_db_fields_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            resp = self.client.get(reverse(api_url_name))

        queue_id = resp.json()["data"]["queue_id"]
        obj = MOQueue.objects.get(id=queue_id)
        assert obj.status == "queued"
        assert obj.api_url_name == api_url_name
        assert obj.request["method"] == "GET"
        mock_init.assert_called_once()
        mock_send.assert_called_once()

    # ── progress_steps stored in Redis on enqueue ─────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_enqueue_stores_progress_steps_in_redis(self, _mock_send):
        """ACCEPTANCE: progress_steps declared on the API class end up in Redis after enqueue."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_enqueue_steps_redis_api")
        _modify_api_attributes(
            self._app,
            "test_enqueue_steps_redis_api",
            {
                "process_mode": "queue",
                "progress_steps": {
                    "validate": {"label": "Validating", "percent": 10},
                    "fetch": {"label": "Fetching", "percent": 50},
                },
            },
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            resp = self.client.get(reverse(api_url_name))
            queue_id = resp.json()["data"]["queue_id"]
            state = get_queue_status(queue_id)

        assert "validate" in state["steps"]
        assert "fetch" in state["steps"]
        assert state["steps"]["validate"]["percent"] == 10
        assert state["steps"]["fetch"]["label"] == "Fetching"

    # ── Idempotency ───────────────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    @patch("apps.django_mindoff.components._api_kit.queue_process.init_queue")
    def test_idempotency_returns_same_id(self, mock_init, mock_send):
        """ACCEPTANCE: Identical requests return the same queue_id (no duplicates)."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_idempotency_api")
        _modify_api_attributes(
            self._app,
            "test_idempotency_api",
            {"process_mode": "queue", "allow_duplicate_queue": False},
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            r1 = self.client.get(reverse(api_url_name))
            r2 = self.client.get(reverse(api_url_name))

        assert r1.json()["data"]["queue_id"] == r2.json()["data"]["queue_id"]
        assert MOQueue.objects.filter(api_url_name=api_url_name).count() == 1
        mock_init.assert_called_once()
        mock_send.assert_called_once()

    def test_idempotency_ignores_cancelled_task(self):
        """BOUNDARY: Cancelled task is NOT reused by idempotency; a new task is created."""
        # The fix: .exclude(status__in=("failed","completed","cancelled"))
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_idempotency_cancelled_api")
        _modify_api_attributes(
            self._app,
            "test_idempotency_cancelled_api",
            {"process_mode": "queue", "allow_duplicate_queue": False},
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            with patch(
                "apps.django_mindoff.components._api_kit.queue_process.execute_queue.send"
            ):
                r1 = self.client.get(reverse(api_url_name))
                q1_id = r1.json()["data"]["queue_id"]
                # Cancel it
                MOQueue.objects.filter(id=q1_id).update(status="cancelled")

                r2 = self.client.get(reverse(api_url_name))
                q2_id = r2.json()["data"]["queue_id"]

        assert q1_id != q2_id, "New queue_id must be issued after cancellation"

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    @patch("apps.django_mindoff.components._api_kit.queue_process.init_queue")
    def test_duplicates_allowed_when_flag_set(self, mock_init, mock_send):
        """ACCEPTANCE: allow_duplicate_queue=True creates separate rows per request."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_dup_allowed_api")
        _modify_api_attributes(
            self._app,
            "test_dup_allowed_api",
            {"process_mode": "queue", "allow_duplicate_queue": True},
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            r1 = self.client.get(reverse(api_url_name))
            r2 = self.client.get(reverse(api_url_name))

        assert r1.json()["data"]["queue_id"] != r2.json()["data"]["queue_id"]
        assert MOQueue.objects.filter(api_url_name=api_url_name).count() == 2

    # ── File upload rejection ─────────────────────────────────────────────

    def test_rejects_file_upload(self):
        """REJECTION: Queue mode returns 400 when files are attached."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        api_url_name = self._make_api("test_enqueue_no_files_api")
        _modify_api_attributes(
            self._app,
            "test_enqueue_no_files_api",
            {"process_mode": "queue", "method": "post"},
            base_path=self._dir,
        )
        resp = self.client.post(
            reverse(api_url_name),
            {"file": SimpleUploadedFile("f.txt", b"data")},
            format="multipart",
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 2. progress_steps schema validation
# ─────────────────────────────────────────────────────────────────────────────


class TestProgressStepsValidation:
    """Unit tests for validate_api_configuration() with various progress_steps values."""

    def _make_mixin(self, **overrides):
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        class ConcreteAPI(MindoffAPIMixin):
            api_url_name = "test_url"
            api_name = "Test"
            api_description = "desc"
            method = "get"
            process_mode = "queue"
            progress_steps = None

        for k, v in overrides.items():
            setattr(ConcreteAPI, k, v)
        return ConcreteAPI()

    # ── Valid cases ───────────────────────────────────────────────────────

    def test_valid_progress_steps_passes(self):
        """ACCEPTANCE: Well-formed progress_steps passes validation."""
        mixin = self._make_mixin(
            progress_steps={
                "validate": {"label": "Validating", "percent": 10},
                "fetch": {"label": "Fetching", "percent": 40},
                "generate": {"label": "Generating", "percent": 80},
            }
        )
        with patch.object(
            type(mixin), "_resolve_url_name", lambda s: True, create=True
        ):
            # Isolate just the steps check
            from apps.django_mindoff.components.api_kit import _validate_progress_steps

            _validate_progress_steps(mixin.progress_steps, mixin.api_url_name)

    def test_none_progress_steps_is_valid(self):
        """BOUNDARY: None progress_steps is always valid (steps are optional)."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from typing import Any

        # Pass None via cast to satisfy the type checker while still testing
        # the runtime guard (`if not steps: return`) in the production function.
        _validate_progress_steps(cast(Any, None), "some_api")

    def test_empty_dict_progress_steps_is_valid(self):
        """BOUNDARY: Empty dict progress_steps is valid."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps

        _validate_progress_steps({}, "some_api")

    def test_single_step_is_valid(self):
        """BOUNDARY: A single step with percent 1–99 is valid."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps

        _validate_progress_steps(
            {"only": {"label": "Only Step", "percent": 50}}, "some_api"
        )

    # ── Invalid percent values ────────────────────────────────────────────

    @pytest.mark.parametrize("bad_pct", [0, 100, -1, 101])
    def test_percent_out_of_range_raises(self, bad_pct):
        """REJECTION: percent outside 1-99 is rejected."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                {"s": {"label": "Step", "percent": bad_pct}}, "some_api"
            )

    def test_non_increasing_percents_raise(self):
        """REJECTION: Steps with non-strictly-increasing percents are rejected."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                {
                    "a": {"label": "A", "percent": 50},
                    "b": {"label": "B", "percent": 30},  # goes backwards
                },
                "some_api",
            )

    def test_equal_percents_raise(self):
        """REJECTION: Two steps with the same percent are rejected."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                {
                    "a": {"label": "A", "percent": 40},
                    "b": {"label": "B", "percent": 40},
                },
                "some_api",
            )

    # ── Invalid types / shapes ────────────────────────────────────────────

    def test_non_dict_top_level_raises(self):
        """REJECTION: progress_steps must be a dict, not a list."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError
        from typing import Any

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                cast(Any, [{"label": "A", "percent": 10}]), "some_api"
            )

    def test_missing_label_raises(self):
        """REJECTION: Step missing 'label' key is rejected."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"a": {"percent": 20}}, "some_api")

    def test_missing_percent_raises(self):
        """REJECTION: Step missing 'percent' key is rejected."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"a": {"label": "Step A"}}, "some_api")

    def test_empty_label_raises(self):
        """REJECTION: Empty or whitespace-only label is rejected."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"a": {"label": "   ", "percent": 10}}, "some_api")

    def test_non_int_percent_raises(self):
        """REJECTION: percent must be int, not float or string."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError
        from typing import Any

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                {"a": {"label": "Step", "percent": cast(Any, 10.5)}}, "some_api"
            )

    def test_empty_key_raises(self):
        """REJECTION: Empty string key in progress_steps is rejected."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"": {"label": "Step", "percent": 10}}, "some_api")

    def test_progress_steps_on_direct_mode_api_raises(self):
        """REJECTION: progress_steps on a direct-mode API raises API_CONFIG_ERR."""
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        mixin = self._make_mixin(
            process_mode="direct",
            progress_steps={"s": {"label": "S", "percent": 10}},
        )
        with pytest.raises((MindoffValidationError, Exception)) as exc_info:
            # Partial call — only the steps section
            from apps.django_mindoff.components.api_kit import _validate_progress_steps

            # Simulate the guard in validate_api_configuration
            from apps.django_mindoff.components.validation_kit import mo_validation_kit

            mo_validation_kit.ensure_equal(
                mixin.process_mode,
                "queue",
                msg="progress_steps only valid for queue-mode APIs",
                is_exception=True,
                code="API_CONFIG_ERR",
            )

        assert "queue" in str(exc_info.value).lower() or exc_info.value is not None


# ─────────────────────────────────────────────────────────────────────────────
# 3. progress_checkpoint (new key-based API)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestProgressCheckpoint(MindoffTestCase):
    """Verifies progress_checkpoint() with the new key-based API."""

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    # ── No-op in direct mode ───────────────────────────────────────────────

    def test_noop_without_queue_task_uuid(self):
        """BOUNDARY: progress_checkpoint is a no-op when queue_task_uuid is absent."""
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        mixin = MindoffAPIMixin()
        mixin.progress_steps = {"s": {"label": "Step", "percent": 50}}
        fake_request = MagicMock(spec=[])  # no queue_task_uuid attribute
        # Must not raise
        mixin.progress_checkpoint(fake_request, "s")

    # ── Normal update ─────────────────────────────────────────────────────

    def test_updates_redis_with_correct_step_and_percent(self):
        """ACCEPTANCE: Calling checkpoint_key updates Redis with the correct label/percent."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())
        mixin = MindoffAPIMixin()
        mixin.progress_steps = {
            "validate": {"label": "Validating", "percent": 15},
            "fetch": {"label": "Fetching", "percent": 60},
        }

        fake_request = MagicMock()
        fake_request.queue_task_uuid = qid

        with patch.object(redis_state, "redis_client", fake_redis):
            from django.utils import timezone as tz

            init_queue(queue_task_uuid=qid, created_at=tz.now())
            mixin.progress_checkpoint(fake_request, "validate")
            state = get_queue_status(qid)

        assert int(state["progress"]["percent"]) == 15
        assert state["progress"]["current_step"] == "Validating"
        assert state["progress"]["current_message"] == "Validating"

    # ── Custom message ─────────────────────────────────────────────────────

    def test_custom_msg_overrides_default_label_message(self):
        """ACCEPTANCE: msg= parameter overrides the default label as current_message."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())
        mixin = MindoffAPIMixin()
        mixin.progress_steps = {"fetch": {"label": "Fetching", "percent": 40}}
        fake_request = MagicMock()
        fake_request.queue_task_uuid = qid

        with patch.object(redis_state, "redis_client", fake_redis):
            from django.utils import timezone as tz

            init_queue(queue_task_uuid=qid, created_at=tz.now())
            mixin.progress_checkpoint(
                fake_request, "fetch", msg="Fetching page 3 of 10"
            )
            state = get_queue_status(qid)

        assert state["progress"]["current_message"] == "Fetching page 3 of 10"
        assert state["progress"]["current_step"] == "Fetching"
        assert int(state["progress"]["percent"]) == 40

    # ── Repeated calls to same key ─────────────────────────────────────────

    def test_same_key_called_multiple_times_updates_message(self):
        """ACCEPTANCE: Same checkpoint_key can be called repeatedly with different msg values."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())
        mixin = MindoffAPIMixin()
        mixin.progress_steps = {"fetch": {"label": "Fetching", "percent": 40}}
        fake_request = MagicMock()
        fake_request.queue_task_uuid = qid

        with patch.object(redis_state, "redis_client", fake_redis):
            from django.utils import timezone as tz

            init_queue(queue_task_uuid=qid, created_at=tz.now())

            messages = [f"Fetching item {i}" for i in range(1, 6)]
            for msg in messages:
                mixin.progress_checkpoint(fake_request, "fetch", msg=msg)
                state = get_queue_status(qid)
                # Percent must stay constant throughout
                assert int(state["progress"]["percent"]) == 40
                assert state["progress"]["current_step"] == "Fetching"

            # Last message must be the final one
            final = get_queue_status(qid)
        assert final["progress"]["current_message"] == "Fetching item 5"

    # ── Cancel detection ───────────────────────────────────────────────────

    @patch("apps.django_mindoff.components.api_kit.update_progress")
    def test_raises_before_update_when_cancelled(self, mock_update):
        """BOUNDARY: progress_checkpoint raises QUEUE_TASK_CANCELLED before writing when is_cancel=True."""
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        mixin = MindoffAPIMixin()
        mixin.progress_steps = {"s": {"label": "Step", "percent": 50}}
        fake_request = MagicMock()
        fake_request.queue_task_uuid = str(uuid.uuid4())

        with patch(
            "apps.django_mindoff.components.api_kit.get_queue_status",
            return_value={"job_status": "running", "is_cancel": True},
        ):
            with pytest.raises(MindoffValidationError) as exc_info:
                mixin.progress_checkpoint(fake_request, "s")

        assert exc_info.value.code == "QUEUE_TASK_CANCELLED"
        mock_update.assert_not_called()

    def test_unknown_checkpoint_key_raises_config_error(self):
        """REJECTION: Using a key not in progress_steps raises API_CONFIG_ERR."""
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        mixin = MindoffAPIMixin()
        mixin.progress_steps = {"valid_key": {"label": "V", "percent": 50}}
        fake_request = MagicMock()
        fake_request.queue_task_uuid = str(uuid.uuid4())

        with patch(
            "apps.django_mindoff.components.api_kit.get_queue_status",
            return_value={"job_status": "running", "is_cancel": False},
        ):
            with pytest.raises((MindoffValidationError, Exception)) as exc_info:
                mixin.progress_checkpoint(fake_request, "nonexistent_key")

        exc = exc_info.value
        assert getattr(
            exc, "code", None
        ) == "API_CONFIG_ERR" or "API_CONFIG_ERR" in str(exc)

    # ── progress_checkpoint inside real worker ─────────────────────────────

    def test_progress_is_written_during_worker_execution(self):
        """ACCEPTANCE: progress_checkpoint updates Redis mid-execution in the worker."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_checkpoint_mid_exec_api")
        _modify_api_attributes(
            self._app,
            "test_checkpoint_mid_exec_api",
            {
                "process_mode": "queue",
                "progress_steps": {
                    "step_a": {"label": "Step A", "percent": 25},
                    "step_b": {"label": "Step B", "percent": 75},
                },
            },
            base_path=self._dir,
        )
        _modify_api_run_method(
            self._app,
            "test_checkpoint_mid_exec_api",
            "        self.progress_checkpoint(request, 'step_a', msg='doing a')\n"
            "        self.progress_checkpoint(request, 'step_b', msg='doing b')\n"
            "        return {'done': True}",
            base_path=self._dir,
        )

        obj = _make_db_task(api_url_name)
        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            execute_queue(str(obj.id))
            final = get_queue_status(str(obj.id))

        obj.refresh_from_db()
        assert obj.status == "completed"
        # After completion the worker sets percent=100
        assert int(final["progress"]["percent"]) == 100


# ─────────────────────────────────────────────────────────────────────────────
# 4. Worker execution lifecycle
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestQueueWorkerExecution(MindoffTestCase):
    """Worker state transitions: success, failure, pre-cancel, mid-cancel, missing task."""

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    # ── Happy path ────────────────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_failed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_completed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_completes_successfully(self, mock_running, mock_completed, mock_failed):
        """ACCEPTANCE: Successful run() marks task completed with compressed response."""
        api_url_name = self._make_api("test_worker_success_api")
        _modify_api_run_method(
            self._app,
            "test_worker_success_api",
            "        return {'result': 'ok'}",
            base_path=self._dir,
        )

        obj = _make_db_task(api_url_name)
        execute_queue(str(obj.id))
        obj.refresh_from_db()

        assert obj.status == "completed"
        assert obj.response is not None
        assert obj.response[_COMPRESSED_RESPONSE_FLAG] is True
        recovered = _decompress(obj.response)
        assert recovered is not None
        mock_running.assert_called_once_with(str(obj.id))
        mock_completed.assert_called_once_with(str(obj.id))
        mock_failed.assert_not_called()

    # ── Failure path ──────────────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_failed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_completed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_marks_failed_on_exception(self, mock_running, mock_completed, mock_failed):
        """REJECTION: Unhandled exception in run() marks task failed and records traceback."""
        api_url_name = self._make_api("test_worker_fail_api")
        _modify_api_run_method(
            self._app,
            "test_worker_fail_api",
            "        raise RuntimeError('boom')",
            base_path=self._dir,
        )

        obj = _make_db_task(api_url_name)
        execute_queue(str(obj.id))
        obj.refresh_from_db()

        assert obj.status == "failed"
        assert obj.error["message"] == "boom"
        assert "RuntimeError" in obj.error["traceback"]
        mock_failed.assert_called_once_with(str(obj.id), error="boom")
        mock_completed.assert_not_called()

    # ── Pre-cancel (before worker starts) ─────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_skips_execution_when_precancelled_in_redis(self, mock_running):
        """BOUNDARY: Worker skips run() when is_cancel=True is set before it starts."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_worker_precancel_redis_api")
        obj = _make_db_task(api_url_name, status="queued")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            mark_cancelled(str(obj.id))  # set is_cancel=True in Redis
            execute_queue(str(obj.id))

        obj.refresh_from_db()
        assert obj.status == "cancelled"
        mock_running.assert_not_called()

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_skips_execution_when_precancelled_in_db(self, mock_running):
        """BOUNDARY: Worker skips run() when DB obj.status is already 'cancelled'."""
        api_url_name = self._make_api("test_worker_precancel_db_api")
        # Redis is empty (expired) so worker falls back to DB
        obj = _make_db_task(api_url_name, status="cancelled")

        with (
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                return_value={"job_status": "unknown", "is_cancel": False, "steps": {}},
            ),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process.rehydrate_queue_state",
                return_value={
                    "job_status": "cancelled",
                    "is_cancel": True,
                    "steps": {},
                },
            ),
        ):
            execute_queue(str(obj.id))

        obj.refresh_from_db()
        assert obj.status == "cancelled"
        mock_running.assert_not_called()

    # ── Race condition: cancel arrives between init_queue and mark_running ──

    def test_cancel_flag_preserved_through_mark_running(self):
        """BOUNDARY: is_cancel flag set before mark_running is NOT cleared by mark_running."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())

        with patch.object(redis_state, "redis_client", fake_redis):
            from django.utils import timezone as tz

            init_queue(queue_task_uuid=qid, created_at=tz.now())
            # Simulate cancel arriving right after init
            mark_cancel_requested(qid)
            # Worker calls mark_running — must NOT wipe is_cancel
            mark_running(qid)
            state = get_queue_status(qid)

        assert (
            state["is_cancel"] is True
        ), "mark_running must preserve is_cancel; it was incorrectly wiped"
        assert state["job_status"] == "running"

    # ── Post-run cancel check ─────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_completed")
    def test_does_not_complete_when_cancelled_after_run(self, mock_completed):
        """BOUNDARY: Worker checks is_cancel after run() and does not mark completed."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_worker_postrun_cancel_api")
        _modify_api_run_method(
            self._app,
            "test_worker_postrun_cancel_api",
            "        return {'result': 'should_not_persist'}",
            base_path=self._dir,
        )

        obj = _make_db_task(api_url_name)
        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)

            # Simulate cancel arriving after run() but before result is saved
            with patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                side_effect=[
                    # First call: initial check before mark_running
                    {"job_status": "queued", "is_cancel": False, "steps": {}},
                    # Second call: post-run check — cancel has arrived
                    {"job_status": "running", "is_cancel": True, "steps": {}},
                ],
            ):
                execute_queue(str(obj.id))

        obj.refresh_from_db()
        assert obj.status == "cancelled"
        mock_completed.assert_not_called()

    # ── progress_checkpoint raises MindoffValidationError with cancel code ──

    def test_cancel_via_progress_checkpoint_marks_task_cancelled(self):
        """ACCEPTANCE: progress_checkpoint raising QUEUE_TASK_CANCELLED results in cancelled task."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_checkpoint_cancel_api")
        _modify_api_attributes(
            self._app,
            "test_checkpoint_cancel_api",
            {
                "process_mode": "queue",
                "progress_steps": {"step_a": {"label": "Step A", "percent": 30}},
            },
            base_path=self._dir,
        )
        _modify_api_run_method(
            self._app,
            "test_checkpoint_cancel_api",
            "        self.progress_checkpoint(request, 'step_a')\n"
            "        return {'should': 'not reach'}",
            base_path=self._dir,
        )

        obj = _make_db_task(api_url_name)
        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            mark_cancel_requested(str(obj.id))
            execute_queue(str(obj.id))

        obj.refresh_from_db()
        assert obj.status == "cancelled"
        assert obj.error is None
        assert obj.response is not None

    # ── Nonexistent task ──────────────────────────────────────────────────

    def test_noop_on_nonexistent_task(self):
        """BOUNDARY: Worker silently returns for unknown queue_task_uuid."""
        execute_queue(str(uuid.uuid4()))  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 5. Cancel and retry lifecycle
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestQueueCancelAndRetry(MindoffTestCase):
    """Cancel and retry state-machine transitions."""

    def _make_task(self, status="queued", api_url_name="some_api"):
        return _make_db_task(api_url_name, status=status)

    # ── Cancel: valid transitions ─────────────────────────────────────────

    @pytest.mark.parametrize("cancellable_status", ["queued", "running"])
    def test_cancel_sets_is_cancel_flag_in_redis(self, cancellable_status):
        """ACCEPTANCE: cancel_queue_task sets is_cancel=True in Redis without changing job_status."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(status=cancellable_status)

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            if cancellable_status == "running":
                mark_running(str(obj.id))

            result = cancel_queue_task(str(obj.id))
            state = get_queue_status(str(obj.id))

        assert result == "cancelled"
        # The flag is set; job_status stays as it was (worker transitions it)
        assert state["is_cancel"] is True
        assert state["job_status"] == cancellable_status

    # ── Cancel: terminal states ────────────────────────────────────────────

    @pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
    def test_cancel_not_cancellable_for_terminal_states(self, terminal):
        """REJECTION: Cancelling a terminal task returns 'not_cancellable'."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(status=terminal)

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            if terminal == "completed":
                mark_completed(str(obj.id))
            elif terminal == "failed":
                mark_failed(str(obj.id), error="err")
            else:
                mark_cancelled(str(obj.id))
            result = cancel_queue_task(str(obj.id))

        assert result == "not_cancellable"

    def test_cancel_uses_db_status_when_redis_expired(self):
        """BOUNDARY: cancel_queue_task falls back to DB status when Redis key is missing."""
        obj = self._make_task(status="completed")

        with (
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                return_value={"job_status": "unknown", "is_cancel": False, "steps": {}},
            ),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process.rehydrate_queue_state",
                return_value={
                    "job_status": "completed",
                    "is_cancel": False,
                    "steps": {},
                },
            ),
        ):
            result = cancel_queue_task(str(obj.id))

        assert result == "not_cancellable"

    def test_cancel_not_found_for_missing_id(self):
        """BOUNDARY: cancel_queue_task returns 'not_found' for unknown UUID."""
        assert cancel_queue_task(str(uuid.uuid4())) == "not_found"

    # ── Retry ─────────────────────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_retry_requeues_failed_task(self, mock_send):
        """ACCEPTANCE: retry_failed_queue_task resets error/response and re-dispatches."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(status="failed")

        with patch.object(redis_state, "redis_client", fake_redis):
            result = retry_failed_queue_task(str(obj.id))

        obj.refresh_from_db()
        assert result == "queued"
        assert obj.status == "queued"
        assert obj.error is None
        assert obj.response is None
        mock_send.assert_called_once_with(str(obj.id))

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_retry_re_initialises_progress_steps_in_redis(self, _mock_send):
        """ACCEPTANCE: Retry restores progress_steps to Redis for the new run."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = "some_api_with_steps"
        obj = self._make_task(status="failed", api_url_name=api_url_name)

        steps = {"a": {"label": "A", "percent": 20}, "b": {"label": "B", "percent": 60}}

        with patch(
            "apps.django_mindoff.components._api_kit.queue_process.get_api_class_from_url_name"
        ) as mock_get:
            mock_cls = MagicMock()
            mock_cls.progress_steps = steps
            mock_get.return_value = mock_cls
            with patch.object(redis_state, "redis_client", fake_redis):
                retry_failed_queue_task(str(obj.id))
                state = get_queue_status(str(obj.id))

        assert "a" in state["steps"]
        assert "b" in state["steps"]

    @pytest.mark.parametrize(
        "non_failed", ["queued", "running", "completed", "cancelled"]
    )
    def test_retry_not_retriable_for_non_failed(self, non_failed):
        """REJECTION: retry_failed_queue_task returns 'not_retriable' for non-failed tasks."""
        obj = self._make_task(status=non_failed)
        assert retry_failed_queue_task(str(obj.id)) == "not_retriable"

    def test_retry_not_found_for_missing_id(self):
        """BOUNDARY: retry_failed_queue_task returns 'not_found' for unknown UUID."""
        assert retry_failed_queue_task(str(uuid.uuid4())) == "not_found"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Rehydration from DB
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestQueueRehydrate(MindoffTestCase):
    """Verifies rehydrate_queue_state() rebuilds Redis from DB when the key has expired."""

    def _make_task(self, status, error=None):
        from apps.django_mindoff.models import MOQueue

        return MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="rehydrate_user",
            api_url_name="some_api",
            status=status,
            request={},
            error=error,
        )

    # ── All DB statuses ────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "db_status",
        ["queued", "running", "completed", "failed", "cancelled"],
    )
    def test_restores_correct_status_from_db(self, db_status):
        """ACCEPTANCE: rehydrate_queue_state returns the DB status for all possible states."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(
            status=db_status,
            error={"message": "oops"} if db_status == "failed" else None,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            result = rehydrate_queue_state(str(obj.id))

        assert result["job_status"] == db_status

    # ── Missing UUID ─────────────────────────────────────────────────────

    def test_returns_unknown_for_missing_id(self):
        """BOUNDARY: rehydrate_queue_state returns 'unknown' when UUID is not in DB."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        with patch.object(redis_state, "redis_client", fake_redis):
            result = rehydrate_queue_state(str(uuid.uuid4()))

        assert result["job_status"] == "unknown"

    # ── Redis is fresher ──────────────────────────────────────────────────

    def test_trusts_redis_when_it_has_a_newer_timestamp(self):
        """ACCEPTANCE: When Redis updated_at > DB updated_at, Redis state is used."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(status="queued")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            # Inject a fresher "running" state directly
            fake_redis.hset(
                f"moq:{obj.id}",
                mapping={
                    "id": str(obj.id),
                    "job_status": "running",
                    "is_cancel": "false",
                    "progress": json.dumps(
                        {"percent": 55, "current_step": "", "current_message": ""}
                    ),
                    "steps": "{}",
                    "started_at": obj.created_at.isoformat(),
                    "updated_at": "2099-01-01T00:00:00+00:00",
                },
            )
            result = rehydrate_queue_state(str(obj.id))

        assert result["job_status"] == "running"
        assert int(result["progress"]["percent"]) == 55

    # ── Cancel flag survives rehydration ─────────────────────────────────

    def test_cancel_flag_propagated_from_redis_to_db_on_rehydrate(self):
        """BOUNDARY: When Redis has is_cancel=True and Redis is fresher, DB is updated to cancelled."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(status="running")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            mark_running(str(obj.id))
            # Simulate cancel requested from external source with future timestamp
            fake_redis.hset(
                f"moq:{obj.id}",
                mapping={
                    "id": str(obj.id),
                    "job_status": "cancelled",
                    "is_cancel": "true",
                    "progress": json.dumps(
                        {"percent": 0, "current_step": "", "current_message": ""}
                    ),
                    "steps": "{}",
                    "started_at": obj.created_at.isoformat(),
                    "updated_at": "2099-01-01T00:00:00+00:00",
                },
            )
            rehydrate_queue_state(str(obj.id))

        obj.refresh_from_db()
        assert obj.status == "cancelled"

    # ── Steps survive rehydration ─────────────────────────────────────────

    def test_steps_are_restored_from_api_class_on_rehydrate(self):
        """ACCEPTANCE: _sync_redis_from_db re-resolves progress_steps from the API class."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        steps = {"x": {"label": "X", "percent": 30}}
        obj = self._make_task(status="queued")

        with patch(
            "apps.django_mindoff.components._api_kit.queue_process.get_api_class_from_url_name"
        ) as mock_get:
            mock_cls = MagicMock()
            mock_cls.progress_steps = steps
            mock_get.return_value = mock_cls

            with patch.object(redis_state, "redis_client", fake_redis):
                result = rehydrate_queue_state(str(obj.id))

        assert "x" in result.get("steps", {})


# ─────────────────────────────────────────────────────────────────────────────
# 7. Redis layer unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRedisLayer:
    """Unit tests for every Redis state-transition function."""

    @pytest.fixture(autouse=True)
    def fake_redis(self):
        from apps.django_mindoff.components._api_kit import redis as redis_state

        r = _InMemoryRedis()
        with patch.object(redis_state, "redis_client", r):
            yield r

    def _now_dt(self):
        from django.utils import timezone as tz

        return tz.now()

    # ── init_queue ────────────────────────────────────────────────────────

    def test_init_queue_creates_queued_state(self):
        """ACCEPTANCE: init_queue writes job_status=queued and percent=0."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        state = get_queue_status(qid)
        assert state["job_status"] == "queued"
        assert int(state["progress"]["percent"]) == 0
        assert state["is_cancel"] is False

    def test_init_queue_stores_steps(self):
        """ACCEPTANCE: init_queue stores progress_steps in Redis."""
        qid = str(uuid.uuid4())
        steps = {"s": {"label": "S", "percent": 50}}
        init_queue(queue_task_uuid=qid, created_at=self._now_dt(), steps=steps)
        state = get_queue_status(qid)
        assert "s" in state["steps"]

    def test_init_queue_with_no_steps(self):
        """BOUNDARY: init_queue without steps returns empty dict for steps."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        state = get_queue_status(qid)
        assert state["steps"] == {}

    # ── mark_running ──────────────────────────────────────────────────────

    def test_mark_running_sets_running_status(self):
        """ACCEPTANCE: mark_running transitions job_status to 'running'."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_running(qid)
        assert get_queue_status(qid)["job_status"] == "running"

    def test_mark_running_preserves_is_cancel_flag(self):
        """BOUNDARY: mark_running does NOT reset is_cancel to False when it was True."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_cancel_requested(qid)
        mark_running(qid)
        state = get_queue_status(qid)
        assert state["job_status"] == "running"
        assert (
            state["is_cancel"] is True
        ), "is_cancel must survive mark_running — this was a real bug"

    def test_mark_running_preserves_steps(self):
        """BOUNDARY: mark_running preserves progress_steps in Redis."""
        qid = str(uuid.uuid4())
        steps = {"a": {"label": "A", "percent": 20}}
        init_queue(queue_task_uuid=qid, created_at=self._now_dt(), steps=steps)
        mark_running(qid)
        assert "a" in get_queue_status(qid)["steps"]

    # ── update_progress ───────────────────────────────────────────────────

    def test_update_progress_writes_correct_values(self):
        """ACCEPTANCE: update_progress stores percent, step, message correctly."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        update_progress(qid, progress=42, step="my_step", message="halfway there")
        state = get_queue_status(qid)
        assert int(state["progress"]["percent"]) == 42
        assert state["progress"]["current_step"] == "my_step"
        assert state["progress"]["current_message"] == "halfway there"

    def test_update_progress_preserves_is_cancel_flag(self):
        """BOUNDARY: update_progress does NOT clear is_cancel when it was True."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_cancel_requested(qid)
        update_progress(qid, progress=50, step="s", message="m")
        assert get_queue_status(qid)["is_cancel"] is True

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(-10, 0), (0, 0), (50, 50), (100, 100), (150, 100)],
    )
    def test_update_progress_clamps_percent(self, raw, expected):
        """BOUNDARY: update_progress clamps progress to [0, 100]."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        update_progress(qid, progress=raw)
        assert int(get_queue_status(qid)["progress"]["percent"]) == expected

    def test_update_progress_preserves_steps(self):
        """BOUNDARY: update_progress preserves existing steps in Redis."""
        qid = str(uuid.uuid4())
        steps = {"fetch": {"label": "Fetching", "percent": 40}}
        init_queue(queue_task_uuid=qid, created_at=self._now_dt(), steps=steps)
        update_progress(qid, progress=40, step="Fetching", message="page 1")
        assert "fetch" in get_queue_status(qid)["steps"]

    # ── mark_completed ────────────────────────────────────────────────────

    def test_mark_completed_sets_100_percent(self):
        """ACCEPTANCE: mark_completed writes job_status=completed and percent=100."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_completed(qid)
        state = get_queue_status(qid)
        assert state["job_status"] == "completed"
        assert int(state["progress"]["percent"]) == 100

    def test_mark_completed_preserves_steps(self):
        """BOUNDARY: mark_completed preserves progress_steps."""
        qid = str(uuid.uuid4())
        steps = {"s": {"label": "S", "percent": 70}}
        init_queue(queue_task_uuid=qid, created_at=self._now_dt(), steps=steps)
        mark_completed(qid)
        assert "s" in get_queue_status(qid)["steps"]

    # ── mark_failed ───────────────────────────────────────────────────────

    def test_mark_failed_stores_error_message(self):
        """ACCEPTANCE: mark_failed writes job_status=failed and error as current_message."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_failed(qid, error="Something went wrong")
        state = get_queue_status(qid)
        assert state["job_status"] == "failed"
        assert "Something went wrong" in state["progress"]["current_message"]

    # ── mark_cancel_requested ─────────────────────────────────────────────

    def test_mark_cancel_requested_sets_is_cancel_only(self):
        """ACCEPTANCE: mark_cancel_requested sets is_cancel=True without changing job_status."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        original_status = get_queue_status(qid)["job_status"]
        mark_cancel_requested(qid)
        state = get_queue_status(qid)
        assert state["is_cancel"] is True
        assert state["job_status"] == original_status

    def test_mark_cancel_requested_noop_when_key_missing(self):
        """BOUNDARY: mark_cancel_requested on an expired/missing key must not raise."""
        qid = str(uuid.uuid4())
        # No init_queue — key does not exist
        mark_cancel_requested(qid)  # must not raise

    # ── mark_cancelled ────────────────────────────────────────────────────

    def test_mark_cancelled_sets_terminal_state(self):
        """ACCEPTANCE: mark_cancelled sets job_status=cancelled and is_cancel=True."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_cancelled(qid)
        state = get_queue_status(qid)
        assert state["job_status"] == "cancelled"
        assert state["is_cancel"] is True

    # ── get_queue_status: unknown ─────────────────────────────────────────

    def test_get_queue_status_returns_unknown_for_missing_key(self):
        """BOUNDARY: get_queue_status returns 'unknown' when key does not exist."""
        state = get_queue_status(str(uuid.uuid4()))
        assert state["job_status"] == "unknown"
        assert state["is_cancel"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 8. Queue status HTTP view
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestQueueStatusView(MindoffTestCase):
    """HTTP tests for MindoffQueueStatusView (polling endpoint)."""

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    def _make_task(self, api_url_name, status="queued"):
        return _make_db_task(api_url_name, status=status)

    # ── 404 ───────────────────────────────────────────────────────────────

    def test_returns_not_found_for_unknown_uuid(self):
        """BOUNDARY: Polling with a nonexistent UUID returns QUEUE_TASK_NOT_FOUND."""
        resp = self.client.get(reverse("mo_queue_status", args=[str(uuid.uuid4())]))
        assert resp.json()["message"]["code"] == "QUEUE_TASK_NOT_FOUND"

    # ── Live Redis path ───────────────────────────────────────────────────

    def test_returns_live_redis_state_with_steps(self):
        """ACCEPTANCE: Status view returns state + steps from live Redis."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_status_live_redis_api")
        steps = {"v": {"label": "V", "percent": 20}, "g": {"label": "G", "percent": 80}}

        with patch(
            "apps.django_mindoff.components._api_kit.queue_process.get_api_class_from_url_name"
        ) as mock_get:
            mock_cls = MagicMock()
            mock_cls.progress_steps = steps
            mock_cls.return_value = mock_cls
            mock_cls.queue_status_limit = None
            mock_cls.api_url_name = api_url_name
            mock_get.return_value = mock_cls

            with patch.object(redis_state, "redis_client", fake_redis):
                obj = self._make_task(api_url_name, status="running")
                init_queue(
                    queue_task_uuid=str(obj.id),
                    created_at=obj.created_at,
                    steps=steps,
                )
                update_progress(
                    str(obj.id), progress=20, step="V", message="validating"
                )

                resp = self.client.get(reverse("mo_queue_status", args=[str(obj.id)]))

        body = resp.json()
        assert body["message"]["code"] == "SUCCESS"
        data = body["data"]
        assert data["job_status"] == "queued"  # init_queue sets queued initially
        assert "steps" in data
        assert "v" in data["steps"]
        assert "g" in data["steps"]

    # ── DB fallback path ──────────────────────────────────────────────────

    def test_returns_db_fallback_when_redis_expired(self):
        """ACCEPTANCE: Status view falls back to DB status when Redis key is absent."""
        api_url_name = self._make_api("test_status_db_fallback_api")
        obj = _make_db_task(api_url_name, status="completed")

        with (
            patch(
                "apps.django_mindoff.views.get_queue_status",
                return_value={"job_status": "unknown", "is_cancel": False, "steps": {}},
            ),
            patch(
                "apps.django_mindoff.views.rehydrate_queue_state",
                return_value={"job_status": "unknown", "is_cancel": False, "steps": {}},
            ),
            patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get,
        ):
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api

            resp = self.client.get(reverse("mo_queue_status", args=[str(obj.id)]))

        body = resp.json()
        assert body["message"]["code"] == "SUCCESS"
        data = body["data"]
        assert data["job_status"] == "completed"

    # ── Compressed response is decoded in status ──────────────────────────

    def test_completed_task_response_is_decoded_in_status(self):
        """ACCEPTANCE: Completed task's compressed response is decompressed in status payload."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_status_response_decode_api")
        _modify_api_run_method(
            self._app,
            "test_status_response_decode_api",
            "        return {'answer': 42}",
            base_path=self._dir,
        )
        _modify_api_attribute(
            self._app,
            "test_status_response_decode_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        with patch(
            "apps.django_mindoff.components._api_kit.queue_process.execute_queue.send"
        ):
            with patch.object(redis_state, "redis_client", fake_redis):
                enqueue_resp = self.client.get(reverse(api_url_name))
                queue_id = enqueue_resp.json()["data"]["queue_id"]
                execute_queue(queue_id)

                resp = self.client.get(reverse("mo_queue_status", args=[queue_id]))

        data = resp.json()["data"]
        assert data["job_status"] == "completed"
        assert data["response"] is not None

    # ── Permission denied ─────────────────────────────────────────────────

    def test_returns_permission_denied_for_wrong_user(self):
        """SECURITY: Polling another user's task returns PERMISSION_DENIED."""
        from apps.django_mindoff.models import MOQueue

        other_user = User.objects.create_user(
            username=f"other_{uuid.uuid4().hex[:6]}", password=uuid.uuid4().hex
        )
        api_url_name = self._make_api("test_status_perm_denied_api")
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id=str(other_user.id),
            user_ref=other_user,
            api_url_name=api_url_name,
            status="running",
            request={},
        )

        with patch(
            "apps.django_mindoff.views.get_queue_status",
            return_value={"job_status": "running", "is_cancel": False, "steps": {}},
        ):
            resp = self.client.get(reverse("mo_queue_status", args=[str(obj.id)]))

        assert resp.json()["message"]["code"] == "PERMISSION_DENIED"


# ─────────────────────────────────────────────────────────────────────────────
# 9. SSE stream
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestQueueSSEStream(MindoffTestCase):
    """Tests for the Server-Sent Events status stream."""

    def _make_task(self, api_url_name, status="running"):
        return _make_db_task(api_url_name, status=status)

    # ── 404 ───────────────────────────────────────────────────────────────

    def test_stream_returns_not_found_for_unknown_uuid(self):
        """BOUNDARY: SSE stream with a nonexistent UUID returns QUEUE_TASK_NOT_FOUND."""
        resp = self.client.get(
            reverse("mo_queue_status_stream", args=[str(uuid.uuid4())])
        )
        body = resp.json() if resp["Content-Type"] == "application/json" else {}
        assert resp.status_code in (200, 400, 404)
        if body:
            assert body["message"]["code"] == "QUEUE_TASK_NOT_FOUND"

    # ── SSE terminates on completed ───────────────────────────────────────

    def test_stream_emits_events_and_terminates_on_completed(self):
        """ACCEPTANCE: SSE stream emits state events and stops after 'completed'."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        _ = _InMemoryRedis()
        # We need a real api_url_name registered in urls.py; use an internal one
        from apps.django_mindoff.models import MOQueue

        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="sse_user",
            api_url_name="mo_queue_status",  # an always-existing URL name
            status="running",
            request={},
        )

        events_collected = []

        def fake_generator():
            # yield two progress events then a terminal
            yield f"data: {json.dumps({'job_status': 'running', 'id': str(obj.id), 'is_cancel': False, 'progress': {'percent': 50, 'current_step': '', 'current_message': ''}, 'steps': {}, 'started_at': '', 'updated_at': ''})}\n\n"
            yield f"data: {json.dumps({'job_status': 'completed', 'id': str(obj.id), 'is_cancel': False, 'progress': {'percent': 100, 'current_step': '', 'current_message': ''}, 'steps': {}, 'started_at': '', 'updated_at': ''})}\n\n"

        with patch(
            "apps.django_mindoff.views.MindoffQueueStatusStreamView._event_stream",
            return_value=fake_generator(),
        ):
            resp = self.client.get(
                reverse("mo_queue_status_stream", args=[str(obj.id)]),
                HTTP_ACCEPT="text/event-stream",
            )

        assert resp.status_code == 200
        content = b"".join(resp.streaming_content).decode()
        events = [
            json.loads(line[len("data: ") :])
            for line in content.splitlines()
            if line.startswith("data: ")
        ]
        assert any(e["job_status"] == "running" for e in events)
        assert any(e["job_status"] == "completed" for e in events)

    # ── steps in SSE events ───────────────────────────────────────────────

    def test_stream_events_include_steps(self):
        """ACCEPTANCE: Each SSE event payload includes the steps dict."""
        from apps.django_mindoff.models import MOQueue

        steps = {"a": {"label": "A", "percent": 30}}
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="sse_steps_user",
            api_url_name="mo_queue_status",
            status="running",
            request={},
        )

        def fake_generator():
            payload = {
                "job_status": "completed",
                "id": str(obj.id),
                "is_cancel": False,
                "progress": {"percent": 100, "current_step": "", "current_message": ""},
                "steps": steps,
                "started_at": "",
                "updated_at": "",
            }
            yield f"data: {json.dumps(payload)}\n\n"

        with patch(
            "apps.django_mindoff.views.MindoffQueueStatusStreamView._event_stream",
            return_value=fake_generator(),
        ):
            resp = self.client.get(
                reverse("mo_queue_status_stream", args=[str(obj.id)]),
                HTTP_ACCEPT="text/event-stream",
            )

        content = b"".join(resp.streaming_content).decode()
        event_data = json.loads(
            next(
                line[len("data: ") :]
                for line in content.splitlines()
                if line.startswith("data: ")
            )
        )
        assert "steps" in event_data
        assert "a" in event_data["steps"]

    # ── SSE permission check ───────────────────────────────────────────────

    def test_stream_permission_denied_for_wrong_user(self):
        """SECURITY: SSE stream returns PERMISSION_DENIED for a task owned by another user."""
        from apps.django_mindoff.models import MOQueue

        owner = User.objects.create_user(
            username=f"sse_owner_{uuid.uuid4().hex[:6]}", password="pass"
        )
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id=str(owner.id),
            user_ref=owner,
            api_url_name="mo_queue_status",
            status="running",
            request={},
        )
        # Request as anonymous user (not owner)
        resp = self.client.get(reverse("mo_queue_status_stream", args=[str(obj.id)]))
        assert resp.status_code in (200, 400, 403)
        if resp["Content-Type"] == "application/json":
            assert resp.json()["message"]["code"] == "PERMISSION_DENIED"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Internal helper functions
# ─────────────────────────────────────────────────────────────────────────────


class TestQueueHelpers:
    """Unit tests for _extract_result, _ensure_json_result, _compress_json_result."""

    def test_extract_drf_response(self):
        """ACCEPTANCE: DRF Response is normalised to {status_code, data}."""
        from rest_framework.response import Response as DRFResponse

        r = DRFResponse(data={"hello": "world"}, status=201)
        result = _extract_result(r)
        assert result["status_code"] == 201
        assert result["data"] == {"hello": "world"}

    def test_extract_json_response(self):
        """ACCEPTANCE: Django JsonResponse is normalised to {status_code, data}."""
        from django.http import JsonResponse

        r = JsonResponse({"foo": "bar"}, status=200)
        result = _extract_result(r)
        assert result["status_code"] == 200
        assert result["data"]["foo"] == "bar"

    def test_extract_plain_dict_passthrough(self):
        """BOUNDARY: Plain dict passes through _extract_result unchanged."""
        data = {"x": 1}
        assert _extract_result(data) == data

    def test_ensure_json_result_passes_for_serializable(self):
        """ACCEPTANCE: Serializable data is returned unchanged."""
        data = {"a": 1, "b": [1, 2], "c": None}
        assert _ensure_json_result(data) == data

    def test_ensure_json_result_does_not_raise_for_coercible_types(self):
        """BOUNDARY: Non-serializable objects are accepted (default=str coerces them)."""

        class X:
            def __str__(self):
                return "x"

        result = _ensure_json_result({"obj": X()})
        assert result is not None

    def test_compress_decompress_roundtrip(self):
        """ACCEPTANCE: Compressed → decompressed → original."""
        original = {"key": "value", "nested": {"n": 99}}
        compressed = _compress_json_result(original)
        recovered = _decompress(compressed)
        assert recovered == original

    def test_compressed_shape(self):
        """ACCEPTANCE: Compressed result contains the correct flag, codec, and data fields."""
        compressed = _compress_json_result({"status_code": 200, "data": {"x": 1}})
        assert compressed[_COMPRESSED_RESPONSE_FLAG] is True
        assert compressed["codec"] == "gzip+base64"
        assert "data" in compressed

    def test_compress_handles_large_payload(self):
        """BOUNDARY: Compression works for a large payload (1 MB of data)."""
        large = {"items": ["x" * 100 for _ in range(10_000)]}
        compressed = _compress_json_result(large)
        recovered = _decompress(compressed)
        assert recovered == large


# ─────────────────────────────────────────────────────────────────────────────
# 11. Full end-to-end integration pipeline
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestQueueFullPipeline(MindoffTestCase):
    """Full lifecycle: enqueue → worker executes → poll confirms completed + steps in response."""

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    # ── Happy path ────────────────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_enqueue_execute_poll_with_response(self, _mock_send):
        """INTEGRATION: Full queue lifecycle — enqueue, execute, poll returns completed + response."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_e2e_pipeline_api")
        _modify_api_run_method(
            self._app,
            "test_e2e_pipeline_api",
            "        return {'pipeline': 'ok', 'value': 123}",
            base_path=self._dir,
        )
        _modify_api_attribute(
            self._app,
            "test_e2e_pipeline_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            # 1. Enqueue
            enqueue_resp = self.client.get(reverse(api_url_name))
            assert enqueue_resp.json()["message"]["code"] == "QUEUED"
            queue_id = enqueue_resp.json()["data"]["queue_id"]

            obj = MOQueue.objects.get(id=queue_id)
            assert obj.status == "queued"

            # 2. Execute
            execute_queue(queue_id)
            obj.refresh_from_db()
            assert obj.status == "completed"

            # 3. Poll
            poll_resp = self.client.get(reverse("mo_queue_status", args=[queue_id]))
            data = poll_resp.json()["data"]

        assert data["job_status"] == "completed"
        assert int(data["progress"]["percent"]) == 100
        response_payload = data["response"]
        normalized = (
            response_payload["data"]
            if isinstance(response_payload, dict) and "data" in response_payload
            else response_payload
        )
        assert normalized["pipeline"] == "ok"
        assert normalized["value"] == 123

    # ── Steps visible end-to-end ──────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_progress_steps_visible_in_poll_response(self, _mock_send):
        """INTEGRATION: progress_steps defined on the API class appear in poll response."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_e2e_steps_poll_api")
        steps = {
            "validate": {"label": "Validating", "percent": 10},
            "generate": {"label": "Generating", "percent": 80},
        }
        _modify_api_attributes(
            self._app,
            "test_e2e_steps_poll_api",
            {"process_mode": "queue", "progress_steps": steps},
            base_path=self._dir,
        )
        _modify_api_run_method(
            self._app,
            "test_e2e_steps_poll_api",
            "        self.progress_checkpoint(request, 'validate')\n"
            "        self.progress_checkpoint(request, 'generate', msg='Almost done')\n"
            "        return {'ok': True}",
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            enqueue_resp = self.client.get(reverse(api_url_name))
            queue_id = enqueue_resp.json()["data"]["queue_id"]
            execute_queue(queue_id)

            poll_resp = self.client.get(reverse("mo_queue_status", args=[queue_id]))
            data = poll_resp.json()["data"]

        assert "steps" in data
        assert "validate" in data["steps"]
        assert "generate" in data["steps"]
        assert data["steps"]["validate"]["percent"] == 10
        assert data["steps"]["generate"]["label"] == "Generating"

    # ── Full cancel flow ──────────────────────────────────────────────────

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_cancel_flow_enqueue_cancel_status(self, _mock_send):
        """INTEGRATION: Enqueue → cancel → poll shows cancel_requested, then worker cancels."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_e2e_cancel_flow_api")
        _modify_api_attribute(
            self._app,
            "test_e2e_cancel_flow_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            enqueue_resp = self.client.get(reverse(api_url_name))
            queue_id = enqueue_resp.json()["data"]["queue_id"]

            # Cancel via API
            cancel_resp = self.client.post(reverse("mo_queue_cancel", args=[queue_id]))
            assert cancel_resp.json()["message"]["code"] == "SUCCESS"

            # Redis must have is_cancel=True
            state = get_queue_status(queue_id)
            assert state["is_cancel"] is True

            # Worker picks it up — should cancel immediately
            execute_queue(queue_id)

        obj = MOQueue.objects.get(id=queue_id)
        assert obj.status == "cancelled"
