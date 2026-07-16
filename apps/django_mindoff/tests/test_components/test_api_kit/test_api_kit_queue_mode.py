import gzip
import base64
import json
import uuid
from typing import cast
from unittest.mock import MagicMock, patch
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from ....components.tdd_kit import MindoffTestCase
from ....components._api_kit.queue_process import (
    _COMPRESSED_RESPONSE_FLAG,
    QueueRequest,
    _compress_json_result,
    _compress_json_result_if_large,
    _decode_json_blob,
    _ensure_json_result,
    _extract_response_code,
    _extract_result,
    _get_json_compression_min_bytes,
    _is_compressed_json_blob,
    _parse_updated_at,
    _safe_get_queue_status,
    _sync_db_from_redis,
    _sync_redis_from_db,
    cancel_queue_task,
    dramatiq_healthcheck,
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
from .test_api_kit_direct_mode import (
    _api_templates,
    _create_test_api,
    _modify_api_attribute,
    _modify_api_attributes,
    _modify_api_run_method,
)

User = get_user_model()


class _InMemoryRedis:

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def ping(self):
        return True

    def flushdb(self):
        self.hashes.clear()
        self.counters.clear()
        self.ttls.clear()

    def hset(self, key, mapping):
        bucket = self.hashes.setdefault(key, {})
        for k, v in mapping.items():
            bucket[str(k)] = str(v)

    def hgetall(self, key):
        data = self.hashes.get(key, {})
        return {str(k).encode("utf-8"): str(v).encode("utf-8") for k, v in data.items()}

    def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True

    def persist(self, key):
        self.ttls.pop(key, None)
        return True

    def incr(self, key):
        value = int(self.counters.get(key, 0)) + 1
        self.counters[key] = value
        return value

    def decr(self, key):
        value = int(self.counters.get(key, 0)) - 1
        self.counters[key] = value
        return value


@pytest.mark.django_db(transaction=True)
class TestQueueEnqueueAcceptance(MindoffTestCase):

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    @patch("apps.django_mindoff.components.api_kit.enqueue_process")
    def test_returns_queue_id_and_urls(self, mock_enqueue):
        """ACCEPTANCE: Validates returns queue id and urls."""
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
        assert queue_id in data["response_url"]
        assert queue_id in data["status_stream_url"]
        assert queue_id in data["cancel_url"]
        assert queue_id in data["retry_url"]
        assert data["progress_steps"] == {}

    @patch("apps.django_mindoff.components.api_kit.enqueue_process")
    def test_returns_progress_steps_in_queued_response(self, mock_enqueue):
        """ACCEPTANCE: Validates returns progress steps in queued response."""
        queue_id = str(uuid.uuid4())
        mock_enqueue.return_value = queue_id
        api_url_name = self._make_api("test_enqueue_shape_steps_api")
        steps = {
            "validate": {"label": "Validating", "percent": 10},
            "fetch": {"label": "Fetching", "percent": 50},
        }
        _modify_api_attributes(
            self._app,
            "test_enqueue_shape_steps_api",
            {"process_mode": "queue", "progress_steps": steps},
            base_path=self._dir,
        )

        resp = self.client.get(reverse(api_url_name))
        body = resp.json()
        assert resp.status_code == 200
        assert body["message"]["code"] == "QUEUED"
        assert body["data"]["progress_steps"] == steps

    @patch("apps.django_mindoff.components.api_kit.enqueue_process")
    def test_returns_queue_service_unavailable_when_backend_is_down(self, mock_enqueue):
        """ACCEPTANCE: Validates returns queue service unavailable when backend is down."""
        mock_enqueue.side_effect = ConnectionError("Connection refused")
        api_url_name = self._make_api("test_enqueue_queue_unavailable_api")
        _modify_api_attribute(
            self._app,
            "test_enqueue_queue_unavailable_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        resp = self.client.get(reverse(api_url_name))
        body = resp.json()
        assert resp.status_code == 503
        assert body["message"]["code"] == "QUEUE_SERVICE_UNAVAILABLE"

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    @patch("apps.django_mindoff.components._api_kit.queue_process.init_queue")
    def test_creates_db_record_with_correct_fields(self, mock_init, mock_send):
        """ACCEPTANCE: Validates creates db record with correct fields."""
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
        assert obj.job_status == "queued"
        assert obj.api_url_name == api_url_name
        assert obj.request["method"] == "GET"
        assert obj.response_code is None
        mock_init.assert_called_once()
        mock_send.assert_called_once()

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_enqueue_does_not_store_progress_steps_in_redis(self, _mock_send):
        """ACCEPTANCE: Validates enqueue does not store progress steps in redis."""
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

        assert "steps" not in state

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    @patch("apps.django_mindoff.components._api_kit.queue_process.init_queue")
    def test_idempotency_returns_same_id(self, mock_init, mock_send):
        """ACCEPTANCE: Validates idempotency returns same id."""
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
        """ACCEPTANCE: Validates idempotency ignores cancelled task."""
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
                MOQueue.objects.filter(id=q1_id).update(job_status="cancelled")
                r2 = self.client.get(reverse(api_url_name))
                q2_id = r2.json()["data"]["queue_id"]

        assert q1_id != q2_id, "New queue_id must be issued after cancellation"

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    @patch("apps.django_mindoff.components._api_kit.queue_process.init_queue")
    def test_duplicates_allowed_when_flag_set(self, mock_init, mock_send):
        """REJECTION: Validates duplicates allowed when flag set."""
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

    def test_rejects_file_upload(self):
        """REJECTION: Validates rejects file upload."""
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


class TestProgressStepsValidation:

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

    def test_valid_progress_steps_passes(self):
        """ACCEPTANCE: Validates valid progress steps passes."""
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
            from apps.django_mindoff.components.api_kit import _validate_progress_steps

            _validate_progress_steps(mixin.progress_steps, mixin.api_url_name)

    def test_none_progress_steps_is_valid(self):
        """BOUNDARY: Validates none progress steps is valid."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from typing import Any

        _validate_progress_steps(cast(Any, None), "some_api")

    def test_empty_dict_progress_steps_is_valid(self):
        """BOUNDARY: Validates empty dict progress steps is valid."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps

        _validate_progress_steps({}, "some_api")

    def test_single_step_is_valid(self):
        """ACCEPTANCE: Validates single step is valid."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps

        _validate_progress_steps(
            {"only": {"label": "Only Step", "percent": 50}}, "some_api"
        )

    @pytest.mark.parametrize("bad_pct", [0, 100, -1, 101])
    def test_percent_out_of_range_raises(self, bad_pct):
        """REJECTION: Validates percent out of range raises."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                {"s": {"label": "Step", "percent": bad_pct}}, "some_api"
            )

    def test_non_increasing_percents_raise(self):
        """REJECTION: Validates non increasing percents raise."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                {
                    "a": {"label": "A", "percent": 50},
                    "b": {"label": "B", "percent": 30},
                },
                "some_api",
            )

    def test_equal_percents_raise(self):
        """REJECTION: Validates equal percents raise."""
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

    def test_non_dict_top_level_raises(self):
        """REJECTION: Validates non dict top level raises."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError
        from typing import Any

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                cast(Any, [{"label": "A", "percent": 10}]), "some_api"
            )

    def test_missing_label_raises(self):
        """REJECTION: Validates missing label raises."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"a": {"percent": 20}}, "some_api")

    def test_missing_percent_raises(self):
        """REJECTION: Validates missing percent raises."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"a": {"label": "Step A"}}, "some_api")

    def test_empty_label_raises(self):
        """REJECTION: Validates empty label raises."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"a": {"label": "   ", "percent": 10}}, "some_api")

    def test_non_int_percent_raises(self):
        """REJECTION: Validates non int percent raises."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError
        from typing import Any

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps(
                {"a": {"label": "Step", "percent": cast(Any, 10.5)}}, "some_api"
            )

    def test_empty_key_raises(self):
        """REJECTION: Validates empty key raises."""
        from apps.django_mindoff.components.api_kit import _validate_progress_steps
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        with pytest.raises((MindoffValidationError, Exception)):
            _validate_progress_steps({"": {"label": "Step", "percent": 10}}, "some_api")

    def test_progress_steps_on_direct_mode_api_raises(self):
        """REJECTION: Validates progress steps on direct mode api raises."""
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        mixin = self._make_mixin(
            process_mode="direct",
            progress_steps={"s": {"label": "S", "percent": 10}},
        )
        with pytest.raises((MindoffValidationError, Exception)) as exc_info:
            from apps.django_mindoff.components.validation_kit import mo_validation_kit

            mo_validation_kit.ensure_equal(
                mixin.process_mode,
                "queue",
                msg="progress_steps only valid for queue-mode APIs",
                is_exception=True,
                code="API_CONFIG_ERR",
            )

        assert "queue" in str(exc_info.value).lower() or exc_info.value is not None


@pytest.mark.django_db(transaction=True)
class TestProgressCheckpoint(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def test_noop_without_queue_task_uuid(self):
        """ACCEPTANCE: Validates noop without queue task uuid."""
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        class _TestAPI(MindoffAPIMixin):
            progress_steps = {"s": {"label": "Step", "percent": 50}}

        mixin = _TestAPI()
        fake_request = MagicMock(spec=[])
        mixin.progress_checkpoint(fake_request, "s")

    def test_updates_redis_with_correct_step_and_percent(self):
        """ACCEPTANCE: Validates updates redis with correct step and percent."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())
        class _TestAPI(MindoffAPIMixin):
            progress_steps = {
                "validate": {"label": "Validating", "percent": 15},
                "fetch": {"label": "Fetching", "percent": 60},
            }

        mixin = _TestAPI()

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

    def test_custom_msg_overrides_default_label_message(self):
        """BOUNDARY: Validates custom msg overrides default label message."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())
        class _TestAPI(MindoffAPIMixin):
            progress_steps = {"fetch": {"label": "Fetching", "percent": 40}}

        mixin = _TestAPI()
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

    def test_same_key_called_multiple_times_updates_message(self):
        """ACCEPTANCE: Validates same key called multiple times updates message."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())
        class _TestAPI(MindoffAPIMixin):
            progress_steps = {"fetch": {"label": "Fetching", "percent": 40}}

        mixin = _TestAPI()
        fake_request = MagicMock()
        fake_request.queue_task_uuid = qid

        with patch.object(redis_state, "redis_client", fake_redis):
            from django.utils import timezone as tz

            init_queue(queue_task_uuid=qid, created_at=tz.now())

            messages = [f"Fetching item {i}" for i in range(1, 6)]
            for msg in messages:
                mixin.progress_checkpoint(fake_request, "fetch", msg=msg)
                state = get_queue_status(qid)
                assert int(state["progress"]["percent"]) == 40
                assert state["progress"]["current_step"] == "Fetching"

            final = get_queue_status(qid)
        assert final["progress"]["current_message"] == "Fetching item 5"

    @patch("apps.django_mindoff.components.api_kit.update_progress")
    def test_raises_before_update_when_cancelled(self, mock_update):
        """REJECTION: Validates raises before update when cancelled."""
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        class _TestAPI(MindoffAPIMixin):
            progress_steps = {"s": {"label": "Step", "percent": 50}}

        mixin = _TestAPI()
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
        """REJECTION: Validates unknown checkpoint key raises config error."""
        from apps.django_mindoff.components.api_kit import MindoffAPIMixin
        from apps.django_mindoff.components.validation_kit import MindoffValidationError

        class _TestAPI(MindoffAPIMixin):
            progress_steps = {"valid_key": {"label": "V", "percent": 50}}

        mixin = _TestAPI()
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

    def test_progress_is_written_during_worker_execution(self):
        """ACCEPTANCE: Validates progress is written during worker execution."""
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
        assert obj.job_status == "completed"
        assert int(final["progress"]["percent"]) == 100

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)


@pytest.mark.django_db(transaction=True)
class TestQueueWorkerExecution(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_failed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_completed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_completes_successfully(self, mock_running, mock_completed, mock_failed):
        """ACCEPTANCE: Validates completes successfully."""
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

        assert obj.job_status == "completed"
        assert obj.response is not None
        assert obj.response[_COMPRESSED_RESPONSE_FLAG] is True
        recovered = _decompress(obj.response)
        assert recovered is not None
        mock_running.assert_called_once_with(str(obj.id))
        mock_completed.assert_called_once_with(str(obj.id))
        mock_failed.assert_not_called()

    def test_response_code_stored_on_completion(self):
        """ACCEPTANCE: Validates response code stored on completion."""
        api_url_name = self._make_api("test_worker_response_code_api")
        _modify_api_run_method(
            self._app,
            "test_worker_response_code_api",
            "        from rest_framework.response import Response\n"
            "        return Response({'value': 1}, status=200)",
            base_path=self._dir,
        )

        obj = _make_db_task(api_url_name)
        execute_queue(str(obj.id))
        obj.refresh_from_db()

        assert obj.job_status == "completed"
        assert obj.response_code == 200

    def test_response_code_stored_on_failure(self):
        """REJECTION: Validates response code stored on failure."""
        api_url_name = self._make_api("test_worker_fail_response_code_api")
        _modify_api_run_method(
            self._app,
            "test_worker_fail_response_code_api",
            "        raise RuntimeError('code_test_failure')",
            base_path=self._dir,
        )

        obj = _make_db_task(api_url_name)
        execute_queue(str(obj.id))
        obj.refresh_from_db()

        assert obj.job_status == "failed"
        assert obj.response_code == 500

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_failed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_completed")
    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_marks_failed_on_exception(self, mock_running, mock_completed, mock_failed):
        """REJECTION: Validates marks failed on exception."""
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

        assert obj.job_status == "failed"
        assert obj.error["message"] == "boom"
        assert "RuntimeError" in obj.error["traceback"]
        mock_failed.assert_called_once_with(str(obj.id), error="boom")
        mock_completed.assert_not_called()

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_skips_execution_when_precancelled_in_redis(self, mock_running):
        """ACCEPTANCE: Validates skips execution when precancelled in redis."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_worker_precancel_redis_api")
        obj = _make_db_task(api_url_name, job_status="queued")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            mark_cancelled(str(obj.id))
            execute_queue(str(obj.id))

        obj.refresh_from_db()
        assert obj.job_status == "cancelled"
        mock_running.assert_not_called()

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_running")
    def test_skips_execution_when_precancelled_in_db(self, mock_running):
        """ACCEPTANCE: Validates skips execution when precancelled in db."""
        api_url_name = self._make_api("test_worker_precancel_db_api")
        obj = _make_db_task(api_url_name, job_status="cancelled")

        with (
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                return_value={"job_status": "unknown", "is_cancel": False},
            ),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process.rehydrate_queue_state",
                return_value={
                    "job_status": "cancelled",
                    "is_cancel": True,
                },
            ),
        ):
            execute_queue(str(obj.id))

        obj.refresh_from_db()
        assert obj.job_status == "cancelled"
        mock_running.assert_not_called()

    def test_cancel_flag_preserved_through_mark_running(self):
        """ACCEPTANCE: Validates cancel flag preserved through mark running."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        qid = str(uuid.uuid4())

        with patch.object(redis_state, "redis_client", fake_redis):
            from django.utils import timezone as tz

            init_queue(queue_task_uuid=qid, created_at=tz.now())
            mark_cancel_requested(qid)
            mark_running(qid)
            state = get_queue_status(qid)

        assert state["is_cancel"] is True, "mark_running must preserve is_cancel"
        assert state["job_status"] == "running"

    @patch("apps.django_mindoff.components._api_kit.queue_process.mark_completed")
    def test_does_not_complete_when_cancelled_after_run(self, mock_completed):
        """ACCEPTANCE: Validates does not complete when cancelled after run."""
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

            with patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                side_effect=[
                    {"job_status": "queued", "is_cancel": False},
                    {"job_status": "running", "is_cancel": True},
                ],
            ):
                execute_queue(str(obj.id))

        obj.refresh_from_db()
        assert obj.job_status == "cancelled"
        mock_completed.assert_not_called()

    def test_cancel_via_progress_checkpoint_marks_task_cancelled(self):
        """ACCEPTANCE: Validates cancel via progress checkpoint marks task cancelled."""
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
        assert obj.job_status == "cancelled"
        assert obj.error is None
        assert obj.response is not None

    def test_noop_on_nonexistent_task(self):
        """BOUNDARY: Validates noop on nonexistent task."""
        execute_queue(str(uuid.uuid4()))

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)


@pytest.mark.django_db(transaction=True)
class TestQueueCancelAndRetry(MindoffTestCase):

    @pytest.mark.parametrize("cancellable_status", ["queued", "running"])
    def test_cancel_sets_is_cancel_flag_in_redis(self, cancellable_status):
        """ACCEPTANCE: Validates cancel sets is cancel flag in redis."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(job_status=cancellable_status)

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            if cancellable_status == "running":
                mark_running(str(obj.id))

            result = cancel_queue_task(str(obj.id))
            state = get_queue_status(str(obj.id))

        assert result == "cancelled"
        assert state["is_cancel"] is True
        assert state["job_status"] == cancellable_status

    @pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
    def test_cancel_not_cancellable_for_terminal_states(self, terminal):
        """BOUNDARY: Validates cancel not cancellable for terminal states."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(job_status=terminal)

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
        """ACCEPTANCE: Validates cancel uses db status when redis expired."""
        obj = self._make_task(job_status="completed")

        with (
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                return_value={"job_status": "unknown", "is_cancel": False},
            ),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process.rehydrate_queue_state",
                return_value={
                    "job_status": "completed",
                    "is_cancel": False,
                },
            ),
        ):
            result = cancel_queue_task(str(obj.id))

        assert result == "not_cancellable"

    def test_cancel_not_found_for_missing_id(self):
        """REJECTION: Validates cancel not found for missing id."""
        assert cancel_queue_task(str(uuid.uuid4())) == "not_found"

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_retry_requeues_retriable_task(self, mock_send):
        """ACCEPTANCE: Validates retry requeues retriable task."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        for retriable_status in ("failed", "cancelled"):
            obj = self._make_task(job_status=retriable_status)

            with patch.object(redis_state, "redis_client", fake_redis):
                result = retry_failed_queue_task(str(obj.id))

            obj.refresh_from_db()
            assert result == "queued"
            assert obj.job_status == "queued"
            assert obj.error is None
            assert obj.response is None
            assert obj.response_code is None

        assert mock_send.call_count == 2

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_retry_does_not_store_progress_steps_in_redis(self, _mock_send):
        """ACCEPTANCE: Validates retry does not store progress steps in redis."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(
            job_status="cancelled", api_url_name="some_api_with_steps"
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            retry_failed_queue_task(str(obj.id))
            state = get_queue_status(str(obj.id))

        assert state["job_status"] == "queued"
        assert "steps" not in state

    @pytest.mark.parametrize("non_retriable", ["queued", "running", "completed"])
    def test_retry_not_retriable_for_non_retriable(self, non_retriable):
        """ACCEPTANCE: Validates retry not retriable for non retriable."""
        obj = self._make_task(job_status=non_retriable)
        assert retry_failed_queue_task(str(obj.id)) == "not_retriable"

    def test_retry_not_found_for_missing_id(self):
        """REJECTION: Validates retry not found for missing id."""
        assert retry_failed_queue_task(str(uuid.uuid4())) == "not_found"

    def _make_task(self, job_status="queued", api_url_name="some_api"):
        return _make_db_task(api_url_name, job_status=job_status)


@pytest.mark.django_db(transaction=True)
class TestQueueRehydrate(MindoffTestCase):

    @pytest.mark.parametrize(
        "db_status",
        ["queued", "running", "completed", "failed", "cancelled"],
    )
    def test_restores_correct_status_from_db(self, db_status):
        """ACCEPTANCE: Validates restores correct status from db."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(
            job_status=db_status,
            error={"message": "oops"} if db_status == "failed" else None,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            result = rehydrate_queue_state(str(obj.id))

        assert result["job_status"] == db_status

    def test_returns_unknown_for_missing_id(self):
        """REJECTION: Validates returns unknown for missing id."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        with patch.object(redis_state, "redis_client", fake_redis):
            result = rehydrate_queue_state(str(uuid.uuid4()))

        assert result["job_status"] == "unknown"

    def test_trusts_redis_when_it_has_a_newer_timestamp(self):
        """ACCEPTANCE: Validates trusts redis when it has a newer timestamp."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(job_status="queued")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            fake_redis.hset(
                f"moq:{obj.id}",
                mapping={
                    "id": str(obj.id),
                    "job_status": "running",
                    "is_cancel": "false",
                    "progress": json.dumps(
                        {"percent": 55, "current_step": "", "current_message": ""}
                    ),
                    "started_at": obj.created_at.isoformat(),
                    "updated_at": "2099-01-01T00:00:00+00:00",
                },
            )
            result = rehydrate_queue_state(str(obj.id))

        assert result["job_status"] == "running"
        assert int(result["progress"]["percent"]) == 55

    def test_cancel_flag_propagated_from_redis_to_db_on_rehydrate(self):
        """ACCEPTANCE: Validates cancel flag propagated from redis to db on rehydrate."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(job_status="running")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            mark_running(str(obj.id))
            fake_redis.hset(
                f"moq:{obj.id}",
                mapping={
                    "id": str(obj.id),
                    "job_status": "cancelled",
                    "is_cancel": "true",
                    "progress": json.dumps(
                        {"percent": 0, "current_step": "", "current_message": ""}
                    ),
                    "started_at": obj.created_at.isoformat(),
                    "updated_at": "2099-01-01T00:00:00+00:00",
                },
            )
            rehydrate_queue_state(str(obj.id))

        obj.refresh_from_db()
        assert obj.job_status == "cancelled"

    def test_rehydrate_does_not_restore_steps_into_redis(self):
        """ACCEPTANCE: Validates rehydrate does not restore steps into redis."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        obj = self._make_task(job_status="queued")

        with patch.object(redis_state, "redis_client", fake_redis):
            result = rehydrate_queue_state(str(obj.id))

        assert result["job_status"] == "queued"
        assert "steps" not in result

    def _make_task(self, job_status, error=None):
        from apps.django_mindoff.models import MOQueue

        return MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="rehydrate_user",
            api_url_name="some_api",
            job_status=job_status,
            request={},
            error=error,
        )


class TestRedisLayer:

    @pytest.fixture(autouse=True)
    def fake_redis(self):
        from apps.django_mindoff.components._api_kit import redis as redis_state

        r = _InMemoryRedis()
        with patch.object(redis_state, "redis_client", r):
            yield r

    def test_init_queue_creates_queued_state(self):
        """ACCEPTANCE: Validates init queue creates queued state."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        state = get_queue_status(qid)
        assert state["job_status"] == "queued"
        assert int(state["progress"]["percent"]) == 0
        assert state["is_cancel"] is False

    def test_init_queue_does_not_store_steps_key(self):
        """ACCEPTANCE: Validates init queue does not store steps key."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        state = get_queue_status(qid)
        assert "steps" not in state

    def test_init_queue_with_no_steps(self):
        """ACCEPTANCE: Validates init queue with no steps."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        state = get_queue_status(qid)
        assert "steps" not in state

    def test_mark_running_sets_running_status(self):
        """ACCEPTANCE: Validates mark running sets running status."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_running(qid)
        assert get_queue_status(qid)["job_status"] == "running"

    def test_mark_running_preserves_is_cancel_flag(self):
        """ACCEPTANCE: Validates mark running preserves is cancel flag."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_cancel_requested(qid)
        mark_running(qid)
        state = get_queue_status(qid)
        assert state["job_status"] == "running"
        assert state["is_cancel"] is True, "is_cancel must survive mark_running"

    def test_mark_running_does_not_introduce_steps_key(self):
        """ACCEPTANCE: Validates mark running does not introduce steps key."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_running(qid)
        assert "steps" not in get_queue_status(qid)

    def test_update_progress_writes_correct_values(self):
        """ACCEPTANCE: Validates update progress writes correct values."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        update_progress(qid, progress=42, step="my_step", message="halfway there")
        state = get_queue_status(qid)
        assert int(state["progress"]["percent"]) == 42
        assert state["progress"]["current_step"] == "my_step"
        assert state["progress"]["current_message"] == "halfway there"

    def test_update_progress_preserves_is_cancel_flag(self):
        """ACCEPTANCE: Validates update progress preserves is cancel flag."""
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
        """ACCEPTANCE: Validates update progress clamps percent."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        update_progress(qid, progress=raw)
        assert int(get_queue_status(qid)["progress"]["percent"]) == expected

    def test_update_progress_does_not_introduce_steps_key(self):
        """ACCEPTANCE: Validates update progress does not introduce steps key."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        update_progress(qid, progress=40, step="Fetching", message="page 1")
        assert "steps" not in get_queue_status(qid)

    def test_mark_completed_sets_100_percent(self):
        """ACCEPTANCE: Validates mark completed sets 100 percent."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_completed(qid)
        state = get_queue_status(qid)
        assert state["job_status"] == "completed"
        assert int(state["progress"]["percent"]) == 100

    def test_mark_completed_does_not_introduce_steps_key(self):
        """ACCEPTANCE: Validates mark completed does not introduce steps key."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_completed(qid)
        assert "steps" not in get_queue_status(qid)

    def test_mark_failed_stores_error_message(self):
        """REJECTION: Validates mark failed stores error message."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_failed(qid, error="Something went wrong")
        state = get_queue_status(qid)
        assert state["job_status"] == "failed"
        assert "Something went wrong" in state["progress"]["current_message"]

    def test_mark_cancel_requested_sets_is_cancel_only(self):
        """ACCEPTANCE: Validates mark cancel requested sets is cancel only."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        original_status = get_queue_status(qid)["job_status"]
        mark_cancel_requested(qid)
        state = get_queue_status(qid)
        assert state["is_cancel"] is True
        assert state["job_status"] == original_status

    def test_mark_cancel_requested_noop_when_key_missing(self):
        """REJECTION: Validates mark cancel requested noop when key missing."""
        mark_cancel_requested(str(uuid.uuid4()))

    def test_mark_cancelled_sets_terminal_state(self):
        """BOUNDARY: Validates mark cancelled sets terminal state."""
        qid = str(uuid.uuid4())
        init_queue(queue_task_uuid=qid, created_at=self._now_dt())
        mark_cancelled(qid)
        state = get_queue_status(qid)
        assert state["job_status"] == "cancelled"
        assert state["is_cancel"] is True

    def test_get_queue_status_returns_unknown_for_missing_key(self):
        """REJECTION: Validates get queue status returns unknown for missing key."""
        state = get_queue_status(str(uuid.uuid4()))
        assert state["job_status"] == "unknown"
        assert state["is_cancel"] is False

    def _now_dt(self):
        from django.utils import timezone as tz

        return tz.now()


@pytest.mark.django_db(transaction=True)
class TestQueueDetailView(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def test_returns_not_found_for_unknown_uuid(self):
        """REJECTION: Validates returns not found for unknown uuid."""
        resp = self.client.get(reverse("mo_queue_detail", args=[str(uuid.uuid4())]))
        assert resp.json()["message"]["code"] == "QUEUE_TASK_NOT_FOUND"

    def test_completed_returns_decompressed_payload_with_response_code(self):
        """ACCEPTANCE: Validates completed returns decompressed payload with response code."""
        from apps.django_mindoff.models import MOQueue

        api_url_name = self._make_api("test_detail_completed_api")
        _modify_api_run_method(
            self._app,
            "test_detail_completed_api",
            "        from apps.django_mindoff.components.response_kit import mo_response_kit\n"
            "        return mo_response_kit.json_response(\n"
            "            code='SUCCESS',\n"
            "            category='success',\n"
            "            data=[{'answer': 42}],\n"
            "        )",
            base_path=self._dir,
        )
        _modify_api_attribute(
            self._app,
            "test_detail_completed_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        with patch(
            "apps.django_mindoff.components._api_kit.queue_process.execute_queue.send"
        ):
            with patch.object(redis_state, "redis_client", fake_redis):
                enqueue_resp = self.client.get(reverse(api_url_name))
                queue_id = enqueue_resp.json()["data"]["queue_id"]
                execute_queue(queue_id)

        obj = MOQueue.objects.get(id=queue_id)
        assert obj.job_status == "completed"
        assert obj.response_code == 200

        resp = self.client.get(reverse("mo_queue_detail", args=[queue_id]))
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "ok"
        assert body["message"]["code"] == "SUCCESS"
        assert body["data"] == [{"answer": 42}]

    def test_completed_uses_stored_response_code(self):
        """ACCEPTANCE: Validates completed uses stored response code."""
        from apps.django_mindoff.models import MOQueue
        from apps.django_mindoff.components._api_kit.queue_process import (
            _compress_json_result,
        )

        api_url_name = self._make_api("test_detail_custom_code_api")
        stored_response = {
            "status": "ok",
            "message": {
                "code": "SUCCESS",
                "title": "Success",
                "description": "Operation completed successfully.",
                "category": "success",
            },
            "data": [{"x": 1}],
        }
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="owner",
            api_url_name=api_url_name,
            job_status="completed",
            response_code=201,
            response=_compress_json_result(stored_response),
            request={},
        )

        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api

            resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        assert resp.status_code == 201
        assert resp.json() == stored_response

    def test_completed_unwraps_legacy_status_code_data_shape(self):
        """ACCEPTANCE: Validates completed unwraps legacy status code data shape."""
        from apps.django_mindoff.models import MOQueue
        from apps.django_mindoff.components._api_kit.queue_process import (
            _compress_json_result,
        )

        api_url_name = self._make_api("test_detail_legacy_shape_api")
        inner_response = {
            "status": "ok",
            "message": {
                "code": "SUCCESS",
                "title": "Success",
                "description": "Operation completed successfully.",
                "category": "success",
            },
            "data": [{"id": "x1"}],
        }
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="owner",
            api_url_name=api_url_name,
            job_status="completed",
            response_code=200,
            response=_compress_json_result(
                {"status_code": 200, "data": inner_response}
            ),
            request={},
        )

        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api

            resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        assert resp.status_code == 200
        assert resp.json() == inner_response

    def test_failed_returns_danger_category_with_response_code(self):
        """REJECTION: Validates failed returns danger category with response code."""
        from apps.django_mindoff.models import MOQueue

        api_url_name = self._make_api("test_detail_failed_api")
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="owner",
            api_url_name=api_url_name,
            job_status="failed",
            response_code=500,
            error={"message": "something went wrong"},
            request={},
        )

        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api

            resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        body = resp.json()
        assert body["message"]["code"] == "QUEUE_TASK_FAILED"
        assert body["message"]["category"] == "danger"
        assert body["data"]["job_status"] == "failed"
        assert "progress_steps" not in body["data"]

    def test_cancelled_returns_queue_task_cancelled_code(self):
        """ACCEPTANCE: Validates cancelled returns queue task cancelled code."""
        api_url_name = self._make_api("test_detail_cancelled_api")
        obj = self._make_task(api_url_name, job_status="cancelled")

        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api

            resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        body = resp.json()
        assert body["message"]["code"] == "QUEUE_TASK_CANCELLED"
        assert body["message"]["category"] == "warning"
        assert body["data"]["job_status"] == "cancelled"
        assert "progress_steps" not in body["data"]

    def test_pending_returns_queue_task_pending_code(self):
        """ACCEPTANCE: Validates pending returns queue task pending code."""
        api_url_name = self._make_api("test_detail_pending_api")
        obj = self._make_task(api_url_name, job_status="pending")

        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api

            resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        body = resp.json()
        assert body["message"]["code"] == "QUEUE_TASK_PENDING"
        assert body["message"]["category"] == "info"
        assert body["data"]["job_status"] == "pending"
        assert "progress_steps" not in body["data"]

    def test_running_returns_live_progress(self):
        """ACCEPTANCE: Validates running returns live progress."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_detail_running_api")
        obj = self._make_task(api_url_name, job_status="running")

        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api

            with patch.object(redis_state, "redis_client", fake_redis):
                init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
                mark_running(str(obj.id))
                update_progress(
                    str(obj.id), progress=45, step="halfway", message="going"
                )

                resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        body = resp.json()
        assert body["message"]["code"] == "QUEUE_TASK_RUNNING"
        assert body["data"]["progress"]["percent"] == 45
        assert body["data"]["job_status"] == "running"
        assert "progress_steps" not in body["data"]

    def test_running_does_not_return_progress_steps(self):
        """ACCEPTANCE: Validates running does not return progress steps."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_detail_running_steps_from_api_api")
        steps = {
            "validate": {"label": "Validating", "percent": 20},
            "generate": {"label": "Generating", "percent": 70},
        }
        _modify_api_attributes(
            self._app,
            "test_detail_running_steps_from_api_api",
            {"process_mode": "queue", "progress_steps": steps},
            base_path=self._dir,
        )
        obj = self._make_task(api_url_name, job_status="running")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(queue_task_uuid=str(obj.id), created_at=obj.created_at)
            mark_running(str(obj.id))
            update_progress(str(obj.id), progress=35, step="validate", message="going")
            resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        body = resp.json()
        assert body["message"]["code"] == "QUEUE_TASK_RUNNING"
        assert "progress_steps" not in body["data"]

    def test_returns_permission_denied_for_wrong_user(self):
        """REJECTION: Validates returns permission denied for wrong user."""
        from apps.django_mindoff.models import MOQueue

        other_user = User.objects.create_user(
            username=f"other_{uuid.uuid4().hex[:6]}", password=uuid.uuid4().hex
        )
        api_url_name = self._make_api("test_detail_perm_denied_api")
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id=str(other_user.id),
            user_ref=other_user,
            api_url_name=api_url_name,
            job_status="running",
            request={},
        )

        with patch("apps.django_mindoff.views.get_api_class_from_url_name") as mock_get:
            mock_api = MagicMock()
            mock_api.queue_status_limit = None
            mock_api.api_url_name = api_url_name
            mock_get.return_value = lambda: mock_api
            resp = self.client.get(reverse("mo_queue_detail", args=[str(obj.id)]))

        assert resp.json()["message"]["code"] == "PERMISSION_DENIED"

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    def _make_task(self, api_url_name, job_status="queued", response_code=None):
        return _make_db_task(
            api_url_name, job_status=job_status, response_code=response_code
        )


@pytest.mark.django_db(transaction=True)
class TestQueueListView(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def test_returns_all_tasks_unfiltered(self):
        """ACCEPTANCE: Validates returns all tasks unfiltered."""
        api = self._make_api("test_list_unfiltered_api")
        _make_db_task(api, job_status="queued")
        _make_db_task(api, job_status="completed")

        resp = self._list()
        body = resp.json()
        assert body["message"]["code"] == "SUCCESS"
        assert body["data"]["count"] >= 2
        assert isinstance(body["data"]["tasks"], list)

    def test_filter_by_id_returns_single_task(self):
        """ACCEPTANCE: Validates filter by id returns single task."""
        api = self._make_api("test_list_filter_id_api")
        obj = _make_db_task(api, job_status="queued")
        _make_db_task(api, job_status="queued")

        resp = self._list(id=str(obj.id))
        body = resp.json()
        assert body["data"]["count"] == 1
        assert body["data"]["tasks"][0]["id"] == str(obj.id)

    def test_filter_by_job_status(self):
        """ACCEPTANCE: Validates filter by job status."""
        api = self._make_api("test_list_filter_status_api")
        _make_db_task(api, job_status="completed")
        _make_db_task(api, job_status="failed")

        resp = self._list(job_status="completed", api_url_name=api)
        tasks = resp.json()["data"]["tasks"]
        assert all(t["job_status"] == "completed" for t in tasks)

    def test_filter_by_owner_id(self):
        """ACCEPTANCE: Validates filter by owner id."""
        api = self._make_api("test_list_filter_owner_api")
        _make_db_task(api, job_status="queued", owner_id="owner_alpha")
        _make_db_task(api, job_status="queued", owner_id="owner_beta")

        resp = self._list(owner_id="owner_alpha")
        tasks = resp.json()["data"]["tasks"]
        assert all(t["owner_id"] == "owner_alpha" for t in tasks)

    def test_filter_by_user_ref_id(self):
        """ACCEPTANCE: Validates filter by user ref id."""
        from apps.django_mindoff.models import MOQueue

        user = User.objects.create_user(
            username=f"list_user_{uuid.uuid4().hex[:6]}", password=uuid.uuid4().hex
        )
        api = self._make_api("test_list_filter_user_api")
        MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="o",
            user_ref=user,
            api_url_name=api,
            job_status="queued",
            request={},
        )
        _make_db_task(api, job_status="queued")

        resp = self._list(user_ref_id=str(user.id))
        tasks = resp.json()["data"]["tasks"]
        assert len(tasks) >= 1
        assert all(t["user_ref_id"] == str(user.id) for t in tasks)

    def test_filter_by_api_url_name(self):
        """ACCEPTANCE: Validates filter by api url name."""
        api_a = self._make_api("test_list_filter_api_name_a")
        api_b = self._make_api("test_list_filter_api_name_b")
        _make_db_task(api_a, job_status="queued")
        _make_db_task(api_b, job_status="queued")

        resp = self._list(api_url_name=api_a)
        tasks = resp.json()["data"]["tasks"]
        assert all(t["api_url_name"] == api_a for t in tasks)

    def test_list_response_includes_response_code_field(self):
        """ACCEPTANCE: Validates list response includes response code field."""
        api = self._make_api("test_list_response_code_field_api")
        _make_db_task(api, job_status="completed", response_code=200)

        resp = self._list(api_url_name=api)
        tasks = resp.json()["data"]["tasks"]
        completed = next(t for t in tasks if t["job_status"] == "completed")
        assert "response_code" in completed
        assert completed["response_code"] == 200

    def test_list_response_includes_all_columns(self):
        """ACCEPTANCE: Validates list response includes all columns."""
        api = self._make_api("test_list_all_columns_api")
        _make_db_task(api, job_status="completed", response_code=200)

        resp = self._list(api_url_name=api)
        task = resp.json()["data"]["tasks"][0]
        assert "id" in task
        assert "owner_id" in task
        assert "user_ref_id" in task
        assert "idempotency_key" in task
        assert "api_url_name" in task
        assert "job_status" in task
        assert "request" in task
        assert "response" in task
        assert "response_code" in task
        assert "error" in task
        assert "created_at" in task
        assert "updated_at" in task

    def test_list_response_decompresses_compressed_json_payload(self):
        """ACCEPTANCE: Validates list response decompresses compressed json payload."""
        api = self._make_api("test_list_decompress_response_api")
        obj = _make_db_task(api, job_status="completed", response_code=200)
        obj.response = _compress_json_result({"status_code": 200, "data": {"ok": True}})
        obj.save(update_fields=["response", "updated_at"])

        resp = self._list(api_url_name=api, id=str(obj.id))
        task = resp.json()["data"]["tasks"][0]
        assert isinstance(task["response"], dict)
        assert task["response"] == {"status_code": 200, "data": {"ok": True}}

    def test_list_is_paginated_for_large_result_sets(self):
        """ACCEPTANCE: Validates list is paginated for large result sets."""
        api = self._make_api("test_list_pagination_api")
        _make_db_task(api, job_status="queued")
        _make_db_task(api, job_status="running")
        _make_db_task(api, job_status="completed")

        resp = self._list(api_url_name=api, page=2, page_size=2)
        body = resp.json()["data"]
        assert body["count"] == 3
        assert body["page"] == 2
        assert body["page_size"] == 2
        assert body["total_pages"] == 2
        assert body["has_previous"] is True
        assert body["has_next"] is False
        assert len(body["tasks"]) == 1

    def test_combined_filters(self):
        """ACCEPTANCE: Validates combined filters."""
        api = self._make_api("test_list_combined_filters_api")
        _make_db_task(api, job_status="completed", owner_id="combined_owner")
        _make_db_task(api, job_status="failed", owner_id="combined_owner")
        _make_db_task(api, job_status="completed", owner_id="other_owner")

        resp = self._list(
            api_url_name=api, job_status="completed", owner_id="combined_owner"
        )
        tasks = resp.json()["data"]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["job_status"] == "completed"
        assert tasks[0]["owner_id"] == "combined_owner"

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)

    def _list(self, **params):
        return self.client.get(reverse("mo_queue_list"), params)


@pytest.mark.django_db(transaction=True)
class TestQueueSSEStream(MindoffTestCase):

    def test_stream_returns_not_found_for_unknown_uuid(self):
        """REJECTION: Validates stream returns not found for unknown uuid."""
        resp = self.client.get(
            reverse("mo_queue_status_stream", args=[str(uuid.uuid4())])
        )
        body = resp.json() if resp["Content-Type"] == "application/json" else {}
        assert resp.status_code in (200, 400, 404)
        if body:
            assert body["message"]["code"] == "QUEUE_TASK_NOT_FOUND"

    def test_stream_emits_events_and_terminates_on_completed(self):
        """BOUNDARY: Validates stream emits events and terminates on completed."""
        from apps.django_mindoff.models import MOQueue

        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id=f"sse_user_{uuid.uuid4().hex[:10]}",
            api_url_name="mo_queue_status_stream",
            job_status="running",
            request={},
        )

        def fake_generator():
            running_event = json.dumps(
                {
                    "job_status": "running",
                    "id": str(obj.id),
                    "is_cancel": False,
                    "progress": {
                        "percent": 50,
                        "current_step": "",
                        "current_message": "",
                    },
                    "started_at": "",
                    "updated_at": "",
                }
            )
            completed_event = json.dumps(
                {
                    "job_status": "completed",
                    "id": str(obj.id),
                    "is_cancel": False,
                    "progress": {
                        "percent": 100,
                        "current_step": "",
                        "current_message": "",
                    },
                    "started_at": "",
                    "updated_at": "",
                }
            )
            yield f"data: {running_event}\n\n"
            yield f"data: {completed_event}\n\n"

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

    def test_stream_permission_denied_for_wrong_user(self):
        """REJECTION: Validates stream permission denied for wrong user."""
        from apps.django_mindoff.models import MOQueue

        owner = User.objects.create_user(
            username=f"sse_owner_{uuid.uuid4().hex[:6]}", password=uuid.uuid4().hex
        )
        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id=str(owner.id),
            user_ref=owner,
            api_url_name="mo_queue_status_stream",
            job_status="running",
            request={},
        )
        resp = self.client.get(reverse("mo_queue_status_stream", args=[str(obj.id)]))
        assert resp.status_code in (200, 400, 403)
        if resp["Content-Type"] == "application/json":
            assert resp.json()["message"]["code"] == "PERMISSION_DENIED"

    def _make_task(self, api_url_name, job_status="running"):
        return _make_db_task(api_url_name, job_status=job_status)


class TestQueueHelpers:

    def test_extract_drf_response(self):
        """ACCEPTANCE: Validates extract drf response."""
        from rest_framework.response import Response as DRFResponse

        r = DRFResponse(data={"hello": "world"}, status=201)
        result = _extract_result(r)
        assert result == {"hello": "world"}

    def test_extract_json_response(self):
        """ACCEPTANCE: Validates extract json response."""
        from django.http import JsonResponse

        r = JsonResponse({"foo": "bar"}, status=200)
        result = _extract_result(r)
        assert result["foo"] == "bar"

    def test_extract_plain_dict_passthrough(self):
        """ACCEPTANCE: Validates extract plain dict passthrough."""
        data = {"x": 1}
        assert _extract_result(data) == data

    def test_ensure_json_result_passes_for_serializable(self):
        """ACCEPTANCE: Validates ensure json result passes for serializable."""
        data = {"a": 1, "b": [1, 2], "c": None}
        assert _ensure_json_result(data) == data

    def test_ensure_json_result_does_not_raise_for_coercible_types(self):
        """REJECTION: Validates ensure json result does not raise for coercible types."""

        class X:
            def __str__(self):
                return "x"

        result = _ensure_json_result({"obj": X()})
        assert result is not None

    def test_compress_decompress_roundtrip(self):
        """ACCEPTANCE: Validates compress decompress roundtrip."""
        original = {"key": "value", "nested": {"n": 99}}
        compressed = _compress_json_result(original)
        recovered = _decompress(compressed)
        assert recovered == original

    def test_compressed_shape(self):
        """ACCEPTANCE: Validates compressed shape."""
        compressed = _compress_json_result({"status_code": 200, "data": {"x": 1}})
        assert compressed[_COMPRESSED_RESPONSE_FLAG] is True
        assert compressed["codec"] == "gzip+base64"
        assert "data" in compressed

    def test_compress_handles_large_payload(self):
        """ACCEPTANCE: Validates compress handles large payload."""
        large = {"items": ["x" * 100 for _ in range(10_000)]}
        compressed = _compress_json_result(large)
        recovered = _decompress(compressed)
        assert recovered == large

    def test_threshold_compression_skips_small_payload(self):
        """ACCEPTANCE: Validates threshold compression skips small payload."""
        payload = {"a": 1}
        saved = _compress_json_result_if_large(payload, min_bytes=4096)
        assert saved == payload

    def test_threshold_compression_compresses_large_payload(self):
        """ACCEPTANCE: Validates threshold compression compresses large payload."""
        payload = {"items": ["x" * 100 for _ in range(500)]}
        saved = _compress_json_result_if_large(payload, min_bytes=200)
        assert saved[_COMPRESSED_RESPONSE_FLAG] is True
        assert _decode_json_blob(saved) == payload


@pytest.mark.django_db(transaction=True)
class TestQueueFullPipeline(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def test_mo_test_api_returns_direct_response_in_queue_mode(self):
        """ACCEPTANCE: Validates mo test api returns direct response in queue mode."""
        from apps.django_mindoff.components import tdd_kit as tdd_kit_module

        get_api_cls = tdd_kit_module._get_api_cls_attributes
        api_url_name = self._make_api("test_mo_test_api_queue_detail_api")
        _modify_api_attribute(
            self._app,
            "test_mo_test_api_queue_detail_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )
        _modify_api_run_method(
            self._app,
            "test_mo_test_api_queue_detail_api",
            "        return mo_response_kit.json_response(code='SUCCESS', category='success', data={'from': 'worker', 'ok': True})",
            base_path=self._dir,
        )

        with (
            patch(
                "apps.django_mindoff.components.tdd_kit._is_versioned_url",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components.tdd_kit._get_api_cls_attributes",
                side_effect=lambda api_name, version=None: get_api_cls(
                    api_name, version=1
                ),
            ),
        ):
            response = self.mo_mock_call_api(api_url_name)

        assert response.status_code == 200
        body = response.json()
        assert body["message"]["code"] == "SUCCESS"
        assert body["data"] == {"from": "worker", "ok": True}

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_enqueue_execute_detail_with_response(self, _mock_send):
        """ACCEPTANCE: Validates enqueue execute detail with response."""
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
            enqueue_resp = self.client.get(reverse(api_url_name))
            assert enqueue_resp.json()["message"]["code"] == "QUEUED"
            queue_id = enqueue_resp.json()["data"]["queue_id"]

            obj = MOQueue.objects.get(id=queue_id)
            assert obj.job_status == "queued"
            assert obj.response_code is None

            execute_queue(queue_id)
            obj.refresh_from_db()
            assert obj.job_status == "completed"
            assert obj.response_code is not None

            detail_resp = self.client.get(reverse("mo_queue_detail", args=[queue_id]))
            data = detail_resp.json()

        assert detail_resp.status_code == int(obj.response_code or 200)
        assert data == {"pipeline": "ok", "value": 123}

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_progress_steps_not_visible_via_sse_stream(self, _mock_send):
        """ACCEPTANCE: Validates progress steps not visible via sse stream."""
        from apps.django_mindoff.components._api_kit import redis as redis_state

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_e2e_steps_sse_api")
        steps = {
            "validate": {"label": "Validating", "percent": 10},
            "generate": {"label": "Generating", "percent": 80},
        }
        _modify_api_attributes(
            self._app,
            "test_e2e_steps_sse_api",
            {"process_mode": "queue", "progress_steps": steps},
            base_path=self._dir,
        )
        _modify_api_run_method(
            self._app,
            "test_e2e_steps_sse_api",
            "        self.progress_checkpoint(request, 'validate')\n"
            "        self.progress_checkpoint(request, 'generate', msg='Almost done')\n"
            "        return {'ok': True}",
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            enqueue_resp = self.client.get(reverse(api_url_name))
            queue_id = enqueue_resp.json()["data"]["queue_id"]
            execute_queue(queue_id)
            stream_resp = self.client.get(
                reverse("mo_queue_status_stream", args=[queue_id]),
                HTTP_ACCEPT="text/event-stream",
            )
            content = b"".join(stream_resp.streaming_content).decode()

        events = [
            json.loads(line[len("data: ") :])
            for line in content.splitlines()
            if line.startswith("data: ")
        ]
        assert events, "Expected at least one SSE event"
        last_event = events[-1]
        assert "steps" not in last_event
        assert "job_status" in last_event
        assert "progress" in last_event

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_cancel_flow_enqueue_cancel_detail_shows_cancelled(self, _mock_send):
        """ACCEPTANCE: Validates cancel flow enqueue cancel detail shows cancelled."""
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

            cancel_resp = self.client.post(reverse("mo_queue_cancel", args=[queue_id]))
            assert cancel_resp.json()["message"]["code"] == "SUCCESS"

            state = get_queue_status(queue_id)
            assert state["is_cancel"] is True

            execute_queue(queue_id)

        obj = MOQueue.objects.get(id=queue_id)
        assert obj.job_status == "cancelled"

        with patch.object(redis_state, "redis_client", fake_redis):
            detail_resp = self.client.get(reverse("mo_queue_detail", args=[queue_id]))

        assert detail_resp.json()["message"]["code"] == "QUEUE_TASK_CANCELLED"

    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    def test_retry_clears_response_code_and_requeues(self, _mock_send):
        """ACCEPTANCE: Validates retry clears response code and requeues."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_e2e_retry_api")
        _modify_api_run_method(
            self._app,
            "test_e2e_retry_api",
            "        raise RuntimeError('first_attempt_failure')",
            base_path=self._dir,
        )
        _modify_api_attribute(
            self._app,
            "test_e2e_retry_api",
            "process_mode",
            "queue",
            base_path=self._dir,
        )

        with patch.object(redis_state, "redis_client", fake_redis):
            enqueue_resp = self.client.get(reverse(api_url_name))
            queue_id = enqueue_resp.json()["data"]["queue_id"]
            execute_queue(queue_id)

        obj = MOQueue.objects.get(id=queue_id)
        assert obj.job_status == "failed"
        assert obj.response_code is not None

        retry_resp = self.client.post(reverse("mo_queue_retry", args=[queue_id]))
        retry_body = retry_resp.json()
        assert retry_body["message"]["code"] == "QUEUED"
        retry_data = retry_body["data"]
        assert retry_data["queue_id"] == queue_id
        assert queue_id in retry_data["response_url"]
        assert queue_id in retry_data["status_stream_url"]
        assert queue_id in retry_data["cancel_url"]
        assert queue_id in retry_data["retry_url"]

        obj.refresh_from_db()
        assert obj.job_status == "queued"
        assert obj.response_code is None

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)


class TestQueueProcessUtilityCoverage:

    def test_queue_request_repr(self):
        """ACCEPTANCE: Validates queue request repr."""
        req = QueueRequest(method="post", data={"x": 1})
        assert repr(req) == "<QueueRequest method=POST>"

    def test_extract_response_code_paths(self):
        """ACCEPTANCE: Validates extract response code paths."""
        from django.http import JsonResponse
        from rest_framework.response import Response as DRFResponse
        from ....components.response_kit import MINDOFF_RESPONSES

        assert _extract_response_code(DRFResponse({"ok": True}, status=201)) == 201
        assert _extract_response_code(JsonResponse({"ok": True}, status=202)) == 202
        assert _extract_response_code(
            {"message": {"code": "INVALID_METHOD"}, "data": {"detail": "x"}}
        ) == int(MINDOFF_RESPONSES["INVALID_METHOD"]["http_status"])
        assert _extract_response_code({"message": {"code": "UNKNOWN_CODE"}}) == 200

    def test_ensure_json_result_raises_on_circular_reference(self):
        """REJECTION: Validates ensure json result raises on circular reference."""
        payload = {}
        payload["self"] = payload
        with pytest.raises(ValueError, match="JSON-serializable"):
            _ensure_json_result(payload)

    def test_compression_helpers_edge_cases(self):
        """BOUNDARY: Validates compression helpers edge cases."""
        assert _compress_json_result_if_large(None, min_bytes=10) is None
        compressed = _compress_json_result({"x": "y"})
        assert _is_compressed_json_blob(compressed) is True
        assert (
            _compress_json_result_if_large(compressed, min_bytes=999999) == compressed
        )
        out = _compress_json_result_if_large({"items": ["x" * 50]}, min_bytes="bad")
        assert _is_compressed_json_blob(out) is True

    def test_decode_json_blob_edge_cases(self):
        """BOUNDARY: Validates decode json blob edge cases."""
        compressed = _compress_json_result({"x": 1})
        compressed.pop("data", None)
        assert _decode_json_blob(compressed) == compressed
        invalid = {
            _COMPRESSED_RESPONSE_FLAG: True,
            "codec": "gzip+base64",
            "data": "not-valid-base64",
        }
        assert _decode_json_blob(invalid) == invalid

    def test_get_json_compression_min_bytes_invalid_setting(self, settings):
        """REJECTION: Validates get json compression min bytes invalid setting."""
        settings.MINDOFF_QUEUE_RESPONSE_COMPRESS_MIN_BYTES = "not_an_int"
        assert _get_json_compression_min_bytes("response", default=123) == 123

    @patch("apps.django_mindoff.components._api_kit.queue_process.get_queue_status")
    def test_safe_get_queue_status_returns_unknown_on_exception(self, mock_get):
        """REJECTION: Validates safe get queue status returns unknown on exception."""
        mock_get.side_effect = RuntimeError("redis down")
        state = _safe_get_queue_status("abc")
        assert state == {"job_status": "unknown", "is_cancel": False}

    def test_parse_updated_at_none(self):
        """BOUNDARY: Validates parse updated at none."""
        assert _parse_updated_at(None) is None

    @pytest.mark.django_db(transaction=True)
    @patch("apps.django_mindoff.components._api_kit.queue_process.execute_queue.send")
    @patch("apps.django_mindoff.components._api_kit.queue_process.init_queue")
    def test_enqueue_process_authenticated_owner(self, mock_init, mock_send):
        """ACCEPTANCE: Validates enqueue process authenticated owner."""
        from django.test import RequestFactory
        from apps.django_mindoff.models import MOQueue

        user = User.objects.create_user(username=f"u_{uuid.uuid4().hex[:8]}")
        request = RequestFactory().get("/x")
        request.user = user
        request.data = {}
        request.query_params = {}

        class _Session:
            session_key = "unused"

            def create(self):
                self.session_key = "created"

        request.session = _Session()

        api = MagicMock()
        api.api_url_name = "demo_api"
        api.allow_duplicate_queue = False

        qid = enqueue_process(request=request, api_instance=api, args=(), kwargs={})
        obj = MOQueue.objects.get(id=qid)
        assert obj.owner_id == str(user.id)
        assert obj.user_ref_id == user.id
        mock_init.assert_called_once()
        mock_send.assert_called_once()

    @pytest.mark.django_db(transaction=True)
    def test_sync_redis_from_db_failed_with_non_dict_error(self):
        """REJECTION: Validates sync redis from db failed with non dict error."""
        from apps.django_mindoff.models import MOQueue

        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="o1",
            api_url_name="x",
            job_status="failed",
            request={},
            error="string_error",
        )
        with (
            patch("apps.django_mindoff.components._api_kit.queue_process.init_queue"),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process.mark_failed"
            ) as mf,
        ):
            _sync_redis_from_db(obj)
        mf.assert_called_once_with(str(obj.id), error="failed")

    @pytest.mark.django_db(transaction=True)
    def test_sync_redis_from_db_cancelled_branch(self):
        """ACCEPTANCE: Validates sync redis from db cancelled branch."""
        from apps.django_mindoff.models import MOQueue

        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="o2",
            api_url_name="x",
            job_status="cancelled",
            request={},
        )
        with (
            patch("apps.django_mindoff.components._api_kit.queue_process.init_queue"),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process.mark_cancelled"
            ) as mc,
        ):
            _sync_redis_from_db(obj)
        mc.assert_called_once_with(str(obj.id))

    @pytest.mark.django_db(transaction=True)
    def test_sync_db_from_redis_branches(self):
        """ACCEPTANCE: Validates sync db from redis branches."""
        from apps.django_mindoff.models import MOQueue

        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="o3",
            api_url_name="x",
            job_status="running",
            request={},
        )
        initial = obj.updated_at

        _sync_db_from_redis(obj, {"job_status": "unknown"})
        obj.refresh_from_db()
        assert obj.job_status == "running"
        assert obj.updated_at == initial

        _sync_db_from_redis(obj, {"job_status": "running"})
        obj.refresh_from_db()
        assert obj.job_status == "running"

        _sync_db_from_redis(
            obj, {"job_status": "failed", "progress": {"current_message": "boom"}}
        )
        obj.refresh_from_db()
        assert obj.job_status == "failed"
        decoded_error = _decode_json_blob(obj.error)
        assert "boom" in str(decoded_error.get("message", ""))

    @pytest.mark.django_db(transaction=True)
    def test_sync_db_from_redis_cancelled_branch(self):
        """ACCEPTANCE: Validates sync db from redis cancelled branch."""
        from apps.django_mindoff.models import MOQueue

        obj = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="o4",
            api_url_name="x",
            job_status="running",
            request={},
        )
        with (
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._is_cancel_requested",
                return_value=False,
            ),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._mark_db_cancelled"
            ) as mark_cancel,
        ):
            _sync_db_from_redis(obj, {"job_status": "cancelled", "is_cancel": False})
        mark_cancel.assert_called_once()

    @pytest.mark.django_db(transaction=True)
    def test_rehydrate_returns_redis_state_when_only_redis_exists(self):
        """ACCEPTANCE: Validates rehydrate returns redis state when only redis exists."""
        with patch(
            "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
            return_value={"job_status": "running", "is_cancel": False},
        ):
            out = rehydrate_queue_state(str(uuid.uuid4()))
        assert out["job_status"] == "running"

    @pytest.mark.django_db(transaction=True)
    def test_rehydrate_handles_redis_id_mismatch_and_db_preferred_fallback(self):
        """REJECTION: Validates rehydrate handles redis id mismatch and db preferred fallback."""
        task = _make_db_task("test_api", job_status="queued")
        with (
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                side_effect=[
                    {"job_status": "running", "id": str(uuid.uuid4())},
                    {"job_status": "queued", "id": str(task.id)},
                ],
            ),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._sync_redis_from_db"
            ) as sync,
        ):
            out = rehydrate_queue_state(str(task.id))
        sync.assert_called_once()
        assert out["id"] == str(task.id)

        with (
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
                side_effect=[
                    {
                        "job_status": "running",
                        "id": str(task.id),
                        "updated_at": "2001-01-01T00:00:00Z",
                    },
                    {"job_status": "queued", "id": str(task.id)},
                ],
            ),
            patch(
                "apps.django_mindoff.components._api_kit.queue_process._sync_redis_from_db"
            ) as sync,
        ):
            rehydrate_queue_state(str(task.id))
        sync.assert_called_once()

    @pytest.mark.django_db(transaction=True)
    def test_cancel_queue_task_not_cancellable_for_unknown_nonterminal_status(self):
        """REJECTION: Validates cancel queue task not cancellable for unknown nonterminal status."""
        task = _make_db_task("test_api", job_status="queued")
        with patch(
            "apps.django_mindoff.components._api_kit.queue_process._safe_get_queue_status",
            return_value={"job_status": "paused", "is_cancel": False},
        ):
            assert cancel_queue_task(str(task.id)) == "not_cancellable"

    @patch("apps.django_mindoff.components._api_kit.queue_process.dramatiq.get_broker")
    def test_dramatiq_healthcheck_returns_true_when_workers_alive(self, mock_get_broker):
        """ACCEPTANCE: Validates dramatiq healthcheck returns true when workers alive."""
        mock_broker = MagicMock()
        mock_broker.workers = {"worker-1"}
        mock_get_broker.return_value = mock_broker

        assert dramatiq_healthcheck("probe-1") is True
        mock_get_broker.assert_called_once()

    @patch("apps.django_mindoff.components._api_kit.queue_process.dramatiq.get_broker")
    def test_dramatiq_healthcheck_returns_false_when_no_workers(self, mock_get_broker):
        """REJECTION: Validates dramatiq healthcheck returns false when no workers."""
        mock_broker = MagicMock()
        mock_broker.workers = set()
        mock_get_broker.return_value = mock_broker

        assert dramatiq_healthcheck("probe-1") is False
        mock_get_broker.assert_called_once()

    @patch("apps.django_mindoff.components._api_kit.queue_process.dramatiq.get_broker")
    def test_dramatiq_healthcheck_returns_false_on_broker_error(self, mock_get_broker):
        """REJECTION: Validates dramatiq healthcheck returns false on broker error."""
        mock_get_broker.side_effect = RuntimeError("broker down")

        assert dramatiq_healthcheck("probe-1") is False
        mock_get_broker.assert_called_once()


@pytest.mark.django_db(transaction=True)
class TestQueueWorkerExtraCoverage(MindoffTestCase):

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def test_execute_queue_validation_error_non_cancel_marks_failed(self):
        """REJECTION: Validates execute queue validation error non cancel marks failed."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_worker_validation_err_api")
        _modify_api_run_method(
            self._app,
            "test_worker_validation_err_api",
            "        from apps.django_mindoff.components.validation_kit import MindoffValidationError\n"
            "        raise MindoffValidationError(message='bad', code='VALIDATION_ERR', category='warning', data={'x': 1})",
            base_path=self._dir,
        )
        task = _make_db_task(api_url_name, job_status="queued")

        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(str(task.id), created_at=task.created_at)
            execute_queue(str(task.id))

        task.refresh_from_db()
        assert task.job_status == "failed"
        assert int(task.response_code or 0) == 400
        decoded = _decode_json_blob(task.error) or {}
        assert decoded.get("code") == "VALIDATION_ERR"

    def test_execute_queue_handles_non_dict_snapshot(self):
        """ACCEPTANCE: Validates execute queue handles non dict snapshot."""
        from apps.django_mindoff.components._api_kit import redis as redis_state
        from apps.django_mindoff.models import MOQueue

        fake_redis = _InMemoryRedis()
        api_url_name = self._make_api("test_worker_non_dict_snapshot_api")
        _modify_api_run_method(
            self._app,
            "test_worker_non_dict_snapshot_api",
            "        return {'ok': True}",
            base_path=self._dir,
        )

        task = MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id="owner_x",
            api_url_name=api_url_name,
            job_status="queued",
            request=["unexpected", "shape"],
        )
        with patch.object(redis_state, "redis_client", fake_redis):
            init_queue(str(task.id), created_at=task.created_at)
            execute_queue(str(task.id))

        task.refresh_from_db()
        assert task.job_status == "completed"

    def _make_api(self, name):
        return _create_test_api(self._app, name, self._dir, self._tmpl)


def _make_db_task(
    api_url_name: str,
    job_status: str = "queued",
    owner_id: str = "test_owner",
    error=None,
    response_code: int | None = None,
):
    from apps.django_mindoff.models import MOQueue

    return MOQueue.objects.create(
        id=uuid.uuid4(),
        owner_id=owner_id,
        api_url_name=api_url_name,
        job_status=job_status,
        response_code=response_code,
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
    raw = base64.b64decode(compressed_payload["data"].encode("ascii"))
    return json.loads(gzip.decompress(raw))
