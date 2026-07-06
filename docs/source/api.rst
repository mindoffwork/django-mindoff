Dramatiq Healthcheck
=====================

The `dramatiq_healthcheck` API provides a way to check the health of the Dramatiq broker.

.. code-block:: python

    from django_mindoff.components._api_kit.queue_process import QueueProcess
    queue_process = QueueProcess()
    result = queue_process.dramatiq_healthcheck()
    self.assertTrue(result)