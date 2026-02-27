from django.urls import path
from .views import (
    MindoffQueuePollingView,
    MindoffQueueStreamingView,
    MindoffQueueCancelView,
    MindoffQueueRetryView,
)

urlpatterns = [
    path(
        "queue/<uuid:queue_task_uuid>/",
        MindoffQueuePollingView.as_view(),
        name="mo_queue_status_polling",
    ),
    path(
        "queue/<uuid:queue_task_uuid>/stream/",
        MindoffQueueStreamingView.as_view(),
        name="mo_queue_status_streaming",
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
