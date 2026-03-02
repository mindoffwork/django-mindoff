from django.urls import path
from .views import (
    MindoffQueueDetailView,
    MindoffQueueListView,
    MindoffQueueStatusStreamView,
    MindoffQueueCancelView,
    MindoffQueueRetryView,
)

urlpatterns = [
    # ── Detail: returns the actual result (or status message) for one task ──
    path(
        "queue/<uuid:queue_task_uuid>/",
        MindoffQueueDetailView.as_view(),
        name="mo_queue_detail",
    ),
    # ── List: filterable index of queue tasks ───────────────────────────────
    # ?id=  ?job_status=  ?user_ref_id=  ?owner_id=  ?api_url_name=
    path(
        "queue/list/",
        MindoffQueueListView.as_view(),
        name="mo_queue_list",
    ),
    # ── SSE stream ──────────────────────────────────────────────────────────
    path(
        "queue/<uuid:queue_task_uuid>/stream/",
        MindoffQueueStatusStreamView.as_view(),
        name="mo_queue_status_stream",
    ),
    # ── Cancel ──────────────────────────────────────────────────────────────
    path(
        "queue/<uuid:queue_task_uuid>/cancel/",
        MindoffQueueCancelView.as_view(),
        name="mo_queue_cancel",
    ),
    # ── Retry ───────────────────────────────────────────────────────────────
    path(
        "queue/<uuid:queue_task_uuid>/retry/",
        MindoffQueueRetryView.as_view(),
        name="mo_queue_retry",
    ),
]
