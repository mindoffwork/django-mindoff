from django.contrib.auth import get_user_model
from django.db import models
from django.conf import settings
import uuid
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class TimeStampModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class MOQueue(TimeStampModel):
    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, db_column="id", editable=False
    )
    owner_id = models.CharField(max_length=128, db_index=True)
    user_ref = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mo_queue_ref_rev",
        db_column="user_ref_id",
    )
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    api_url_name = models.TextField()

    # Renamed from `status` → `job_status`
    job_status = models.CharField(max_length=20, db_column="job_status")

    request = models.JSONField(null=True, blank=True)

    # Stores only the raw response payload (no response_code embedded here)
    response = models.JSONField(null=True, blank=True)

    # HTTP status code from the queued API response
    # (e.g. 200, 400, 404, 500).
    response_code = models.PositiveSmallIntegerField(null=True, blank=True)

    error = models.JSONField(null=True, blank=True)

    def get_user(self):
        if not self.user_ref:
            return None
        from django.contrib.auth import get_user_model

        return get_user_model().objects.filter(id=self.user_ref).first()

    class Meta:
        db_table = "tbl_mo_queue"
        indexes = [
            models.Index(fields=["user_ref_id", "job_status"]),
        ]
