from django_mindoff.components._api_kit.queue_process import QueueProcess

class QueueWorker:
    def __init__(self):
        self.queue_process = QueueProcess()

    def execute_queue(self):
        # Current implementation
        pass

    def dramatiq_healthcheck(self):
        return self.queue_process.dramatiq_healthcheck()