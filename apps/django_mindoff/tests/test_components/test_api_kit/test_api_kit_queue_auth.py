"""
Authentication and ownership for the generic queue endpoints.

The queue endpoints are addressed by ``queue_task_uuid`` and borrow the
authentication scheme of the API that created the task, because the framework
cannot know which scheme a consumer app uses.

These tests deliberately use **real credentials** instead of
``force_authenticate``: DRF's ``force_authenticate`` replaces
``request.authenticators`` wholesale, so a test built on it would pass even if
the resolution under test were deleted outright.
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token

from ....components._api_kit.redis import (
    consume_stream_ticket,
    issue_stream_ticket,
    redis_client,
    stream_ticket_key,
)
from ....components.tdd_kit import MindoffTestCase
from ....models import MOQueue
from ....views import (
    _origin_api_url_name,
    _origin_authentication_classes,
    _queue_list_authentication_classes,
    _scope_queue_queryset,
)
from .test_api_kit_direct_mode import (
    _api_templates,
    _create_test_api,
    _modify_api_attributes,
)

User = get_user_model()

TASK_ENDPOINTS = [
    ("mo_queue_detail", "get"),
    ("mo_queue_status_stream", "get"),
    ("mo_queue_cancel", "post"),
    ("mo_queue_retry", "post"),
]


class _QueueAuthMixin(MindoffTestCase):
    """Shared scaffolding: a queue-mode API sitting behind TokenAuthentication."""

    @pytest.fixture(autouse=True)
    def shared_app(self, _api_templates):
        app_name, temp_dir_path = self.mo_mock_app(is_return_path=True)
        self._app = app_name
        self._dir = temp_dir_path
        self._tmpl = _api_templates

    def _token_api(self, name):
        api_url_name = _create_test_api(self._app, name, self._dir, self._tmpl)
        _modify_api_attributes(
            self._app,
            name,
            {
                "process_mode": "queue",
                "authentication_classes": "[TokenAuthentication]",
            },
            base_path=self._dir,
        )
        return api_url_name

    def _user(self, prefix):
        user = User.objects.create_user(
            username=f"{prefix}_{uuid.uuid4().hex[:8]}",
            password=uuid.uuid4().hex,
        )
        return user, Token.objects.create(user=user).key

    def _task(self, api_url_name, *, owner=None, job_status="queued"):
        return MOQueue.objects.create(
            id=uuid.uuid4(),
            owner_id=str(owner.id) if owner is not None else "anonymous-owner",
            user_ref=owner,
            api_url_name=api_url_name,
            job_status=job_status,
            request={},
        )

    def _call(self, url_name, task, *, token=None, method="get", **params):
        url = reverse(url_name, args=[str(task.id)])
        if params:
            url = f"{url}?" + "&".join(f"{k}={v}" for k, v in params.items())
        extra = {"HTTP_AUTHORIZATION": f"Token {token}"} if token else {}
        resp = getattr(self.client, method)(url, **extra)
        self._drain(resp)
        return resp

    @staticmethod
    def _drain(resp):
        """Consume a streaming body so its SSE slot is released."""
        if hasattr(resp, "streaming_content"):
            resp._drained = b"".join(resp.streaming_content).decode()
        return getattr(resp, "_drained", "")

    @staticmethod
    def _code(resp):
        """Return the Mindoff response code, or None for a raw SSE stream."""
        if resp.get("Content-Type", "").startswith("text/event-stream"):
            return None
        try:
            return resp.json()["message"]["code"]
        except Exception:
            return None

    @staticmethod
    def _is_denied(resp):
        return resp.status_code == 403


@pytest.mark.django_db(transaction=True)
class TestQueueTaskOwnerAuthentication(_QueueAuthMixin):

    @pytest.mark.parametrize(("url_name", "method"), TASK_ENDPOINTS)
    def test_owner_is_not_denied_on_any_task_endpoint(self, url_name, method):
        """ACCEPTANCE: Validates owner is not denied on any task endpoint."""
        api = self._token_api("test_queue_auth_owner_api")
        owner, token = self._user("owner")
        task = self._task(api, owner=owner, job_status="completed")

        resp = self._call(url_name, task, token=token, method=method)

        assert not self._is_denied(resp)
        assert self._code(resp) != "PERMISSION_DENIED"

    @pytest.mark.parametrize(("url_name", "method"), TASK_ENDPOINTS)
    def test_other_authenticated_user_is_denied(self, url_name, method):
        """REJECTION: Validates other authenticated user is denied."""
        api = self._token_api("test_queue_auth_stranger_api")
        owner, _ = self._user("owner")
        _, stranger_token = self._user("stranger")
        task = self._task(api, owner=owner, job_status="completed")

        resp = self._call(url_name, task, token=stranger_token, method=method)

        assert self._is_denied(resp)

    @pytest.mark.parametrize(("url_name", "method"), TASK_ENDPOINTS)
    def test_unauthenticated_caller_is_denied_on_owned_task(self, url_name, method):
        """REJECTION: Validates unauthenticated caller is denied on owned task."""
        api = self._token_api("test_queue_auth_anon_api")
        owner, _ = self._user("owner")
        task = self._task(api, owner=owner, job_status="completed")

        resp = self._call(url_name, task, method=method)

        assert self._is_denied(resp)

    @pytest.mark.parametrize(("url_name", "method"), TASK_ENDPOINTS)
    def test_ownerless_task_stays_anonymously_reachable(self, url_name, method):
        """ACCEPTANCE: Validates ownerless task stays anonymously reachable."""
        api = self._token_api("test_queue_auth_ownerless_api")
        task = self._task(api, owner=None, job_status="completed")

        resp = self._call(url_name, task, method=method)

        assert not self._is_denied(resp)
        assert self._code(resp) != "PERMISSION_DENIED"

    def test_owner_detail_returns_the_task_payload(self):
        """ACCEPTANCE: Validates owner detail returns the task payload."""
        api = self._token_api("test_queue_auth_detail_api")
        owner, token = self._user("owner")
        task = self._task(api, owner=owner, job_status="pending")

        resp = self._call("mo_queue_detail", task, token=token)

        assert resp.status_code == 200
        assert resp.json()["message"]["code"] == "QUEUE_TASK_PENDING"
        assert resp.json()["data"]["queue_id"] == str(task.id)

    def test_owner_can_retry_and_cancel_own_task(self):
        """ACCEPTANCE: Validates owner can retry and cancel own task."""
        api = self._token_api("test_queue_auth_lifecycle_api")
        owner, token = self._user("owner")
        task = self._task(api, owner=owner, job_status="failed")

        retry = self._call("mo_queue_retry", task, token=token, method="post")
        assert retry.json()["message"]["code"] == "QUEUED"

        cancel = self._call("mo_queue_cancel", task, token=token, method="post")
        assert cancel.json()["message"]["code"] == "SUCCESS"

    def test_public_origin_api_keeps_its_endpoints_public(self):
        """ACCEPTANCE: Validates public origin api keeps its endpoints public."""
        api = _create_test_api(
            self._app, "test_queue_auth_public_api", self._dir, self._tmpl
        )
        task = self._task(api, owner=None, job_status="pending")

        resp = self._call("mo_queue_detail", task)

        assert resp.status_code == 200
        assert resp.json()["message"]["code"] == "QUEUE_TASK_PENDING"

    def test_unresolvable_origin_api_denies_anonymous_on_owned_task(self):
        """REJECTION: Validates unresolvable origin api denies anonymous on owned task."""
        owner, _ = self._user("owner")
        task = self._task("api_removed_since", owner=owner, job_status="pending")

        resp = self._call("mo_queue_detail", task)

        assert self._is_denied(resp)

    def test_unresolvable_origin_api_allows_ownerless_task(self):
        """ACCEPTANCE: Validates unresolvable origin api allows ownerless task."""
        task = self._task("api_removed_since", owner=None, job_status="pending")

        resp = self._call("mo_queue_detail", task)

        assert resp.status_code == 200
        assert resp.json()["message"]["code"] == "QUEUE_TASK_PENDING"

    def test_unknown_task_still_reports_not_found(self):
        """REJECTION: Validates unknown task still reports not found."""
        resp = self.client.get(reverse("mo_queue_detail", args=[str(uuid.uuid4())]))
        assert resp.json()["message"]["code"] == "QUEUE_TASK_NOT_FOUND"


@pytest.mark.django_db(transaction=True)
class TestQueueStreamTicket(_QueueAuthMixin):

    def _issue(self, task, token):
        return self._call("mo_queue_stream_ticket", task, token=token, method="post")

    def test_owner_receives_a_ticket(self):
        """ACCEPTANCE: Validates owner receives a ticket."""
        api = self._token_api("test_ticket_issue_api")
        owner, token = self._user("owner")
        task = self._task(api, owner=owner, job_status="completed")

        resp = self._issue(task, token)
        body = resp.json()

        assert resp.status_code == 200
        assert body["message"]["code"] == "SUCCESS"
        assert body["data"]["ticket"]
        assert body["data"]["expires_in"] >= 1
        assert body["data"]["ticket"] in body["data"]["status_stream_url"]

    def test_ticket_authenticates_a_stream_without_any_header(self):
        """ACCEPTANCE: Validates ticket authenticates a stream without any header."""
        api = self._token_api("test_ticket_stream_api")
        owner, token = self._user("owner")
        task = self._task(api, owner=owner, job_status="completed")
        ticket = self._issue(task, token).json()["data"]["ticket"]

        resp = self._call("mo_queue_status_stream", task, ticket=ticket)

        assert not self._is_denied(resp)
        assert resp["Content-Type"].startswith("text/event-stream")

    def test_ticket_is_single_use(self):
        """REJECTION: Validates ticket is single use."""
        api = self._token_api("test_ticket_single_use_api")
        owner, token = self._user("owner")
        task = self._task(api, owner=owner, job_status="completed")
        ticket = self._issue(task, token).json()["data"]["ticket"]

        first = self._call("mo_queue_status_stream", task, ticket=ticket)
        second = self._call("mo_queue_status_stream", task, ticket=ticket)

        assert not self._is_denied(first)
        assert self._is_denied(second)

    def test_ticket_is_bound_to_its_own_task(self):
        """REJECTION: Validates ticket is bound to its own task."""
        api = self._token_api("test_ticket_task_bound_api")
        owner, token = self._user("owner")
        task_a = self._task(api, owner=owner, job_status="completed")
        task_b = self._task(api, owner=owner, job_status="completed")
        ticket = self._issue(task_a, token).json()["data"]["ticket"]

        resp = self._call("mo_queue_status_stream", task_b, ticket=ticket)

        assert self._is_denied(resp)

    def test_stranger_cannot_obtain_a_ticket(self):
        """REJECTION: Validates stranger cannot obtain a ticket."""
        api = self._token_api("test_ticket_stranger_api")
        owner, _ = self._user("owner")
        _, stranger_token = self._user("stranger")
        task = self._task(api, owner=owner, job_status="completed")

        resp = self._issue(task, stranger_token)

        assert self._is_denied(resp)

    def test_unknown_task_has_no_ticket(self):
        """REJECTION: Validates unknown task has no ticket."""
        resp = self.client.post(
            reverse("mo_queue_stream_ticket", args=[str(uuid.uuid4())])
        )
        assert resp.json()["message"]["code"] == "QUEUE_TASK_NOT_FOUND"

    def test_garbage_ticket_falls_through_to_normal_authentication(self):
        """REJECTION: Validates garbage ticket falls through to normal authentication."""
        api = self._token_api("test_ticket_garbage_api")
        owner, _ = self._user("owner")
        task = self._task(api, owner=owner, job_status="completed")

        resp = self._call("mo_queue_status_stream", task, ticket="not-a-real-ticket")

        assert self._is_denied(resp)

    def test_ownerless_ticket_leaves_the_caller_anonymous(self):
        """BOUNDARY: Validates ownerless ticket leaves the caller anonymous."""
        api = self._token_api("test_ticket_ownerless_api")
        task = self._task(api, owner=None, job_status="completed")
        ticket = self._issue(task, None).json()["data"]["ticket"]

        resp = self._call("mo_queue_status_stream", task, ticket=ticket)

        assert not self._is_denied(resp)


class TestStreamTicketStore:
    """Unit-level behaviour of the Redis-backed ticket store."""

    def test_raw_ticket_is_never_stored(self):
        """ACCEPTANCE: Validates raw ticket is never stored."""
        task_id = str(uuid.uuid4())
        ticket = issue_stream_ticket(queue_task_uuid=task_id, user_ref_id=None)
        try:
            assert ticket not in stream_ticket_key(ticket)
            assert redis_client.hgetall(stream_ticket_key(ticket))
        finally:
            redis_client.delete(stream_ticket_key(ticket))

    def test_empty_ticket_is_invalid(self):
        """REJECTION: Validates empty ticket is invalid."""
        assert consume_stream_ticket("", queue_task_uuid="x") == (False, None)

    def test_unknown_ticket_is_invalid(self):
        """REJECTION: Validates unknown ticket is invalid."""
        assert consume_stream_ticket("nope", queue_task_uuid="x") == (False, None)

    def test_valid_ticket_returns_bound_user(self):
        """ACCEPTANCE: Validates valid ticket returns bound user."""
        task_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        ticket = issue_stream_ticket(queue_task_uuid=task_id, user_ref_id=user_id)
        assert consume_stream_ticket(ticket, queue_task_uuid=task_id) == (True, user_id)

    def test_mismatched_task_burns_the_ticket(self):
        """REJECTION: Validates mismatched task burns the ticket."""
        task_id = str(uuid.uuid4())
        ticket = issue_stream_ticket(queue_task_uuid=task_id, user_ref_id=None)
        assert consume_stream_ticket(ticket, queue_task_uuid="other") == (False, None)
        assert consume_stream_ticket(ticket, queue_task_uuid=task_id) == (False, None)


@pytest.mark.django_db(transaction=True)
class TestQueueListScoping(_QueueAuthMixin):

    def _list(self, **params):
        return self.client.get(reverse("mo_queue_list"), params)

    def _login(self, prefix, *, is_staff=False):
        password = uuid.uuid4().hex
        username = f"{prefix}_{uuid.uuid4().hex[:8]}"
        user = User.objects.create_user(
            username=username, password=password, is_staff=is_staff
        )
        self.client.login(username=username, password=password)
        return user

    def test_authenticated_user_sees_only_own_tasks(self):
        """ACCEPTANCE: Validates authenticated user sees only own tasks."""
        api = self._token_api("test_scope_own_api")
        stranger, _ = self._user("stranger")
        self._task(api, owner=stranger)
        self._task(api, owner=None)
        user = self._login("member")
        mine = self._task(api, owner=user)

        tasks = self._list().json()["data"]["tasks"]

        assert [t["id"] for t in tasks] == [str(mine.id)]

    def test_staff_sees_every_task(self):
        """ACCEPTANCE: Validates staff sees every task."""
        api = self._token_api("test_scope_staff_api")
        stranger, _ = self._user("stranger")
        self._task(api, owner=stranger)
        self._task(api, owner=None)
        self._login("admin", is_staff=True)

        assert self._list().json()["data"]["count"] >= 2

    def test_anonymous_without_session_sees_nothing(self):
        """REJECTION: Validates anonymous without session sees nothing."""
        api = self._token_api("test_scope_anon_api")
        stranger, _ = self._user("stranger")
        self._task(api, owner=stranger)
        self._task(api, owner=None)

        body = self._list().json()["data"]

        assert body["count"] == 0
        assert body["tasks"] == []

    def test_anonymous_sees_only_own_session_tasks(self):
        """ACCEPTANCE: Validates anonymous sees only own session tasks."""
        api = self._token_api("test_scope_session_api")
        session = self.client.session
        session.save()
        self.client.cookies["sessionid"] = session.session_key
        mine = self._task(api, owner=None)
        MOQueue.objects.filter(id=mine.id).update(owner_id=session.session_key)
        self._task(api, owner=None)

        tasks = self._list().json()["data"]["tasks"]

        assert [t["id"] for t in tasks] == [str(mine.id)]

    def test_user_ref_id_param_cannot_widen_scope(self):
        """REJECTION: Validates user ref id param cannot widen scope."""
        api = self._token_api("test_scope_no_widen_api")
        stranger, _ = self._user("stranger")
        self._task(api, owner=stranger)
        self._login("member")

        body = self._list(user_ref_id=str(stranger.id)).json()["data"]

        assert body["count"] == 0

    def test_configured_authentication_classes_are_used(self):
        """ACCEPTANCE: Validates configured authentication classes are used."""
        api = self._token_api("test_scope_configured_auth_api")
        owner, token = self._user("owner")
        mine = self._task(api, owner=owner)
        with override_settings(
            MINDOFF_QUEUE_LIST_AUTHENTICATION_CLASSES=[
                "rest_framework.authentication.TokenAuthentication",
            ]
        ):
            resp = self.client.get(
                reverse("mo_queue_list"), HTTP_AUTHORIZATION=f"Token {token}"
            )

        tasks = resp.json()["data"]["tasks"]
        assert [t["id"] for t in tasks] == [str(mine.id)]


class TestQueueAuthHelpers:
    """Fallback branches of the resolution helpers, which must never raise."""

    def test_origin_api_url_name_none_for_empty_uuid(self):
        """BOUNDARY: Validates origin api url name none for empty uuid."""
        assert _origin_api_url_name(None) is None
        assert _origin_api_url_name("") is None

    @pytest.mark.django_db
    def test_origin_api_url_name_none_for_unknown_task(self):
        """REJECTION: Validates origin api url name none for unknown task."""
        assert _origin_api_url_name(uuid.uuid4()) is None

    @pytest.mark.django_db
    def test_origin_api_url_name_swallows_database_errors(self):
        """BOUNDARY: Validates origin api url name swallows database errors."""
        assert _origin_api_url_name("not-a-uuid") is None

    def test_origin_authentication_classes_none_for_unknown_route(self):
        """REJECTION: Validates origin authentication classes none for unknown route."""
        assert _origin_authentication_classes("no_such_route") is None

    def test_origin_authentication_classes_empty_for_public_api(self):
        """ACCEPTANCE: Validates origin authentication classes empty for public api."""
        assert _origin_authentication_classes("mo_queue_detail") == []

    def test_queue_list_authentication_defaults_to_drf_settings(self):
        """ACCEPTANCE: Validates queue list authentication defaults to drf settings."""
        from rest_framework.settings import api_settings

        assert _queue_list_authentication_classes() == list(
            api_settings.DEFAULT_AUTHENTICATION_CLASSES
        )

    def test_queue_list_authentication_skips_unimportable_paths(self):
        """BOUNDARY: Validates queue list authentication skips unimportable paths."""
        from rest_framework.authentication import BasicAuthentication

        with override_settings(
            MINDOFF_QUEUE_LIST_AUTHENTICATION_CLASSES=[
                "nope.NotAThing",
                "rest_framework.authentication.BasicAuthentication",
                42,
            ]
        ):
            assert _queue_list_authentication_classes() == [BasicAuthentication]

    @pytest.mark.django_db
    def test_scope_queue_queryset_empty_without_identity(self):
        """REJECTION: Validates scope queue queryset empty without identity."""

        class _Anon:
            is_authenticated = False

        class _Req:
            user = _Anon()
            session = None

        assert not _scope_queue_queryset(MOQueue.objects.all(), _Req()).exists()
