from automation.src.runtime.environments import EnvironmentPool, EnvironmentProfile
from automation.src.runtime.scheduler import RunScheduler, ScheduledTask


class FakeClient:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "run-1", "status": {"status": "passed"}, "kwargs": kwargs}


def test_environment_pool_selects_by_tags_and_preference():
    pool = EnvironmentPool({
        "local": EnvironmentProfile("local", "http://local", tags=("local", "fast"), max_parallel=2),
        "test": EnvironmentProfile("test", "http://test", tags=("test", "real"), max_parallel=1),
    })

    assert pool.select(required_tags=["real"]).name == "test"
    assert pool.select(required_tags=["fast"], preferred="local").name == "local"


def test_scheduler_passes_environment_overrides():
    client = FakeClient()
    pool = EnvironmentPool({
        "test": EnvironmentProfile(
            "test",
            "http://127.0.0.1:9400",
            ai_base_url="http://127.0.0.1:9401",
            tags=("test", "real"),
            max_parallel=1,
        )
    })
    scheduler = RunScheduler(client=client, pool=pool)

    result = scheduler.run(ScheduledTask(scenario="real_smoke", profile="pro", tags=("real",)))

    assert result["status"]["status"] == "passed"
    assert client.calls[0]["env"] == "test"
    assert client.calls[0]["env_overrides"]["OPENROBOT_API_BASE_URL"] == "http://127.0.0.1:9400"
    assert client.calls[0]["env_overrides"]["AI_EVAL_BASE_URL"] == "http://127.0.0.1:9401"


def test_scheduler_run_many_returns_all_results():
    client = FakeClient()
    pool = EnvironmentPool({
        "local": EnvironmentProfile("local", "http://local", tags=("fast",), max_parallel=2),
    })
    scheduler = RunScheduler(client=client, pool=pool)

    results = scheduler.run_many(
        [
            ScheduledTask(scenario="api_mock", profile="fast", tags=("fast",)),
            ScheduledTask(scenario="business_chain", profile="fast", tags=("fast",)),
        ],
        max_workers=2,
    )

    assert len(results) == 2
    assert len(client.calls) == 2