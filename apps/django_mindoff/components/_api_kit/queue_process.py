from dramatiq import broker_healthcheck

class QueueProcess:
    def dramatiq_healthcheck(self):
        return broker_healthcheck()