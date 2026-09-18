import pytest

from automation.src.reporting.ticket_writeback import build_ticket_report, writeback_ticket_report
from automation.src.runtime import RunContext
from automation.src.runtime.trace_store import TraceStore


def test_build_ticket_report_contains_failure_diagnostics(tmp_path):
    store = TraceStore(run_root=tmp_path / "runs")
    context = RunContext.create(scenario="real_lifecycle", env="test")
    store.start_run(context)
    store.record_step(context.run_id, {"step": "test", "status": "failed", "error": "Connection refused"})
    store.finish_run(context.run_id, "failed", exit_code=1, summary="environment failure")

    report = build_ticket_report(context.run_id, store=store)

    assert report["run_id"] == context.run_id
    assert report["status"] == "failed"
    assert report["failure_class"] == "environment_unreachable"
    assert report["run_dir"]

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="ok"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self):
        self.calls = []

    async def connect(self):
        self.calls.append("connect")

    async def close(self):
        self.calls.append("close")

    async def post(self, path, **kwargs):
        self.calls.append(("post", path, kwargs))
        return FakeResponse(payload={"access_token": "token"})

    async def patch(self, path, **kwargs):
        self.calls.append(("patch", path, kwargs))
        return FakeResponse()


@pytest.mark.asyncio
async def test_writeback_ticket_report_updates_metadata(tmp_path):
    store = TraceStore(run_root=tmp_path / "runs")
    context = RunContext.create(scenario="real_lifecycle", env="test")
    store.start_run(context)
    store.finish_run(context.run_id, "passed", exit_code=0)
    client = FakeAsyncClient()

    result = await writeback_ticket_report(
        837,
        context.run_id,
        username="u1",
        password="p1",
        client=client,
        store=store,
    )

    assert result["status"] == "updated"
    assert ("patch", "/api/tasks/837") in [call[:2] for call in client.calls]