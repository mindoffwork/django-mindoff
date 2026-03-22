import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

# Import actors so Dramatiq discovers and registers them on worker startup.
from .components._api_kit.queue_process import dramatiq_healthcheck, execute_queue

__all__ = ["execute_queue", "dramatiq_healthcheck"]
