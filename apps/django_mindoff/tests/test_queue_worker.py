"""
Smoke and regression tests for queue_worker.py.

queue_worker.py is the production Dramatiq entrypoint: it calls django.setup(),
warms the ROOT_URLCONF, and imports the actor functions so the worker process
discovers them on startup.

Two kinds of tests live here:

* In-process smoke/unit tests, which never start a worker or touch the broker.
* Cold-subprocess tests, which spawn a real fresh interpreter. Worker boot order
  is only observable in a process that has not already imported Django's URLconf,
  and the pytest process always has (the suite reverses URLs), so these
  assertions are impossible to make in-process.
"""

import importlib
import os
import subprocess
import sys
from functools import cached_property
from pathlib import Path
from unittest.mock import patch

import pytest

from ..components import helper_kit
from ..components.helper_kit import _ensure_urlconf_loaded, _warm_up_urlconf

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_SETTINGS = "apps.django_mindoff.tests.settings"


def _run_cold(code: str) -> str:
    """Run code in a fresh interpreter rooted at the repo, return its stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": TEST_SETTINGS,
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"subprocess failed ({result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return result.stdout


@pytest.fixture(scope="module")
def queue_worker():
    # django.setup() inside queue_worker is idempotent when Django is already
    # configured (which it is via --ds). os.environ.setdefault won't override the
    # already-set DJANGO_SETTINGS_MODULE, so the test settings stay in place.
    return importlib.import_module("apps.django_mindoff.queue_worker")


class _FakeResolver:
    """Stands in for Django's URLResolver: url_patterns is a cached_property."""

    def __init__(self, patterns=(), error=None):
        self.touches = 0
        self._patterns = patterns
        self._error = error

    @cached_property
    def url_patterns(self):
        self.touches += 1
        if self._error is not None:
            raise self._error
        return self._patterns


# ----------------
# Module surface
# ----------------
def test_module_is_importable(queue_worker):
    assert queue_worker is not None


def test_all_declares_expected_actors(queue_worker):
    assert set(queue_worker.__all__) == {"execute_queue", "dramatiq_healthcheck"}


def test_execute_queue_is_callable(queue_worker):
    assert callable(queue_worker.execute_queue)


def test_dramatiq_healthcheck_is_callable(queue_worker):
    assert callable(queue_worker.dramatiq_healthcheck)


# ----------------
# Boot-time URLconf warm-up
# ----------------
BOOT_CODE = """
import importlib
importlib.import_module("apps.django_mindoff.queue_worker")
from django.urls import get_resolver
print("LOADED=%s" % ("url_patterns" in get_resolver().__dict__))
"""

CONTROL_CODE = """
import django
django.setup()
from django.urls import get_resolver
print("LOADED=%s" % ("url_patterns" in get_resolver().__dict__))
"""


def test_importing_queue_worker_loads_urlconf_at_boot():
    """A cold worker must resolve ROOT_URLCONF before it can accept a task.

    Regression guard: when this import was deferred, the first queued task to
    reach a freshly-started worker paid the whole URLconf import inside
    execute_queue's catch-all handler, so any import error in the project URL
    graph surfaced as that task failing with an unrelated traceback.
    """
    assert "LOADED=True" in _run_cold(BOOT_CODE)


def test_django_setup_alone_does_not_load_urlconf():
    """Control for the test above.

    django.setup() populates the app registry but never imports ROOT_URLCONF.
    Without this assertion the boot test would still pass if the warm-up were
    deleted and something else happened to import the URLconf first.
    """
    assert "LOADED=False" in _run_cold(CONTROL_CODE)


# ----------------
# Cold concurrent first resolution
# ----------------
CONCURRENT_CODE = """
import django, threading, traceback
django.setup()
from django.urls import get_resolver
assert "url_patterns" not in get_resolver().__dict__, "resolver was not cold"

from apps.django_mindoff.components.helper_kit import mo_helper_kit

# Two different queued endpoints: this is a generic dispatch path, not one route.
NAMES = ["mo_queue_detail", "mo_queue_list"] * 6
barrier = threading.Barrier(len(NAMES))
resolved, errors = [], []

def go(name):
    barrier.wait()
    try:
        resolved.append(mo_helper_kit.get_api_class_from_url_name(api_url_name=name))
    except BaseException:
        errors.append(traceback.format_exc())

threads = [threading.Thread(target=go, args=(n,)) for n in NAMES]
for t in threads:
    t.start()
for t in threads:
    t.join()

print("RESOLVED=%d" % len(resolved))
print("ERRORS=%d" % len(errors))
for e in errors:
    print(e)
"""


def test_cold_concurrent_resolution_across_two_endpoints():
    """Many threads first-touching a cold URLconf must all resolve cleanly."""
    out = _run_cold(CONCURRENT_CODE)
    assert "ERRORS=0" in out, out
    assert "RESOLVED=12" in out, out


# ----------------
# _ensure_urlconf_loaded
# ----------------
def test_ensure_urlconf_loaded_returns_root_resolver():
    from django.urls import get_resolver

    resolver = _ensure_urlconf_loaded()
    assert resolver is get_resolver()
    assert "url_patterns" in resolver.__dict__


def test_ensure_urlconf_loaded_imports_once_then_uses_fast_path():
    fake = _FakeResolver(patterns=[])
    with patch.object(helper_kit, "get_resolver", return_value=fake):
        assert _ensure_urlconf_loaded() is fake
        assert fake.touches == 1
        # Second call must short-circuit on the cached value, not re-import.
        assert _ensure_urlconf_loaded() is fake
        assert fake.touches == 1


def test_ensure_urlconf_loaded_propagates_import_errors():
    fake = _FakeResolver(error=ImportError("boom"))
    with patch.object(helper_kit, "get_resolver", return_value=fake):
        with pytest.raises(ImportError, match="boom"):
            _ensure_urlconf_loaded()


# ----------------
# _warm_up_urlconf
# ----------------
def test_warm_up_urlconf_returns_true_on_success():
    fake = _FakeResolver(patterns=[])
    with patch.object(helper_kit, "get_resolver", return_value=fake):
        assert _warm_up_urlconf() is True


def test_warm_up_urlconf_logs_traceback_and_keeps_booting(caplog):
    """A broken URLconf must be loud at startup but must not kill the worker."""
    fake = _FakeResolver(error=ImportError("cannot import name 'Thing'"))
    with patch.object(helper_kit, "get_resolver", return_value=fake):
        with caplog.at_level("ERROR", logger=helper_kit.__name__):
            assert _warm_up_urlconf() is False

    records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert records, "warm-up failure must be logged at ERROR"
    assert "ROOT_URLCONF" in records[0].getMessage()
    assert records[0].exc_info is not None, "traceback must be included"


def test_warm_up_urlconf_is_invoked_at_module_import():
    """queue_worker must invoke the warm-up, not merely import it."""
    source = (Path(__file__).resolve().parents[1] / "queue_worker.py").read_text(
        encoding="utf-8"
    )
    assert "_warm_up_urlconf()" in source


def test_fake_resolver_matches_django_cached_property_semantics():
    """Guards the stand-in itself: instance __dict__ shadows after first read."""
    from django.urls import get_resolver

    real = get_resolver()
    real.url_patterns
    assert "url_patterns" in real.__dict__

    fake = _FakeResolver(patterns=[])
    assert "url_patterns" not in fake.__dict__
    fake.url_patterns
    assert "url_patterns" in fake.__dict__
