import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

# Import actors so Dramatiq discovers and registers them on worker startup.
# dramatiq_healthcheck is also exported here for backward compatibility but is
# now a regular function that probes broker worker liveness directly.
from .components._api_kit.queue_process import dramatiq_healthcheck, execute_queue
from .components.helper_kit import _warm_up_urlconf

# Import the ROOT_URLCONF graph now, on the process main thread, while nothing
# else is running. Dramatiq re-imports this module in every worker subprocess
# and only starts worker threads once that import returns, so this runs exactly
# once per worker before any task can be picked up.
#
# Without it, the first queued task to reach a cold worker pays the whole
# URLconf import inside execute_queue's catch-all handler: an import error
# anywhere in the project's URL graph is then recorded as *that task* failing,
# with a traceback naming some unrelated module, and never reaches the worker
# startup log.
#
# Deliberately placed after the actor import rather than directly after
# django.setup(): the actor chain already loads most of what the URL graph
# needs (DRF, the models, the mindoff views), so warming up here costs ~18ms of
# boot instead of ~62ms, and the first task saves the ~23ms it used to spend.
# Net import work is unchanged -- it just happens where it can be seen.
_warm_up_urlconf()

__all__ = ["execute_queue", "dramatiq_healthcheck"]
