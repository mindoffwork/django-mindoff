import unittest
from django_mindoff.components._api_kit.queue_process import QueueProcess
from dramatiq import broker_healthcheck

class TestQueueProcess(unittest.TestCase):
    def test_dramatiq_healthcheck(self):
        queue_process = QueueProcess()
        result = queue_process.dramatiq_healthcheck()
        self.assertTrue(result)