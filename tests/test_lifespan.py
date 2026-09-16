import asyncio

from app import main


class FakeScheduler:
    def __init__(self):
        self.jobs = []
        self.running = False

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def start(self):
        self.running = True

    def shutdown(self, wait=True):
        self.running = False


def test_startup_schedules_first_cycle_immediately_without_blocking(monkeypatch):
    # Arrange: a first cycle can take many minutes (hundreds of LLM calls
    # after downtime), so running it inline would keep the server from
    # answering /health until it finished.
    fake = FakeScheduler()
    monkeypatch.setattr(main, "scheduler", fake)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "setup_logging", lambda _dir: None)

    def fail_if_called():
        raise AssertionError("cycle_job must not run inline during startup")

    monkeypatch.setattr(main, "cycle_job", fail_if_called)

    async def run_lifespan():
        async with main.lifespan(main.app):
            assert fake.running

    # Act
    asyncio.run(run_lifespan())

    # Assert
    [(func, trigger, kwargs)] = fake.jobs
    assert trigger == "interval"
    assert kwargs["next_run_time"] is not None
