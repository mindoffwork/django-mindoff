from django.urls import path
from .views import (
    MindoffQueueStatusView,
    MindoffQueueStatusStreamView,
    MindoffQueueCancelView,
    MindoffQueueRetryView,
)

urlpatterns = [
    path(
        "queue/<uuid:queue_task_uuid>/",
        MindoffQueueStatusView.as_view(),
        name="mo_queue_status",
    ),
    path(
        "queue/<uuid:queue_task_uuid>/stream/",
        MindoffQueueStatusStreamView.as_view(),
        name="mo_queue_status_stream",
    ),
    path(
        "queue/<uuid:queue_task_uuid>/cancel/",
        MindoffQueueCancelView.as_view(),
        name="mo_queue_cancel",
    ),
    path(
        "queue/<uuid:queue_task_uuid>/retry/",
        MindoffQueueRetryView.as_view(),
        name="mo_queue_retry",
    ),
]
