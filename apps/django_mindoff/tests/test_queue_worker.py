"""
Smoke tests for queue_worker.py.

queue_worker.py is the production Dramatiq entrypoint: it calls django.setup()
and imports the actor functions so the worker process discovers them on startup.
These tests verify the module is importable and exposes the expected public surface,
without starting a real worker or connecting to Redis/a broker.
"""

import importlib

import pytest


@pytest.fixture(scope="module")
def queue_worker():
    # django.setup() inside queue_worker is idempotent when Django is already
    # configured (which it is via --ds). os.environ.setdefault won't override the
    # already-set DJANGO_SETTINGS_MODULE, so the test settings stay in place.
    return importlib.import_module("apps.django_mindoff.queue_worker")


def test_module_is_importable(queue_worker):
    assert queue_worker is not None


def test_all_declares_expected_actors(queue_worker):
    assert set(queue_worker.__all__) == {"execute_queue", "dramatiq_healthcheck"}


def test_execute_queue_is_callable(queue_worker):
    assert callable(queue_worker.execute_queue)


def test_dramatiq_healthcheck_is_callable(queue_worker):
    assert callable(queue_worker.dramatiq_healthcheck)
