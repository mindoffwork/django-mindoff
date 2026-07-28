import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

# Import actors so Dramatiq discovers and registers them on worker startup.
# dramatiq_healthcheck is also exported here for backward compatibility but is
# now a regular function that probes broker worker liveness directly.
from .components._api_kit.queue_process import dramatiq_healthcheck, execute_queue

__all__ = ["execute_queue", "dramatiq_healthcheck"]
