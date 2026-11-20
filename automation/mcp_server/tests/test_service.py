import json

import pytest

from automation.mcp_server import service
from automation.src.ai_metrics import JudgeUnavailableError, LLMJudgeClient


class FakeStore:
    def __init__(self, root):
        self.root = root

    def run_dir(self, run_id):
        return self.root / run_id


class FakeClient:
    def __init__(self, run_data, root):
        self.run_data = run_data
        self.store = FakeStore(root)
        self.calls = []

    def health(self):
        return {"status": "healthy", "checks": {"test": True}, "run_root": str(self.store.root)}

    def submit(self, **kwargs):
        self.calls.append(("submit", kwargs))
        return self.run_data

    def run(self, **kwargs):
        self.calls.append(("run", kwargs))
        return self.run_data

    def get_run(self, run_id):
        return self.run_data


def _run_data(root):
    return {
        "run_id": "run-001",
        "run": {
            "run_id": "run-001",
            "trace_id": "tr-001",
            "scenario": "business_chain",
            "profile": "fast",
            "env": "local",
            "started_at": "2026-09-17T00:00:00+00:00",
        },
        "status": {
            "run_id": "run-001",
            "trace_id": "tr-001",
            "status": "passed",
            "exit_code": 0,
            "summary": "ok",
            "started_at": "2026-09-17T00:00:00+00:00",
            "finished_at": "2026-09-17T00:00:01+00:00",
        },
        "steps": [{"step": "login"}, {"step": "create_ticket"}],
    }


def test_run_scenario_submit_and_wait(tmp_path):
    client = FakeClient(_run_data(tmp_path), tmp_path)

    submitted = service.run_scenario("business_chain", client=client)
    waited = service.run_scenario("business_chain", wait=True, client=client)

    assert submitted["status"] == "passed"
    assert waited["trace_id"] == "tr-001"
    assert [call[0] for call in client.calls] == ["submit", "run"]


def test_get_run_status_and_inspect_trace(tmp_path):
    client = FakeClient(_run_data(tmp_path), tmp_path)
    artifact_dir = client.store.run_dir("run-001") / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "pytest.log").write_text("ok", encoding="utf-8")

    status = service.get_run_status("run-001", client=client)
    trace = service.inspect_trace("run-001", client=client)

    assert status["status"] == "passed"
    assert trace["step_count"] == 2
    assert trace["steps"][0]["step"] == "login"
    assert trace["artifacts"] == [str(artifact_dir / "pytest.log")]


def test_diagnose_environment_without_external_checks(tmp_path, monkeypatch):
    client = FakeClient(_run_data(tmp_path), tmp_path)
    monkeypatch.setattr(service.importlib.util, "find_spec", lambda name: object())

    result = service.diagnose_environment(env="local", check_external=False, client=client)

    assert result["verdict"] == "ready"
    assert result["checks"]["api"]["skipped"] is True
    assert result["next_steps"] == ["Environment is ready for the selected profile"]


def test_run_ai_eval_routes_to_ai_scenario(tmp_path):
    client = FakeClient(_run_data(tmp_path), tmp_path)

    result = service.run_ai_eval(suite="DIAG", client=client)

    assert result["run_id"] == "run-001"
    assert client.calls[0][1]["scenario"] == "ai_eval"
    assert client.calls[0][1]["extra_args"] == ["-k", "DIAG"]


@pytest.mark.asyncio
async def test_generate_ticket_cases_reports_missing_llm(tmp_path, monkeypatch):
    def unavailable(cls, project_root=None):
        raise JudgeUnavailableError("LLM not configured")

    monkeypatch.setattr(LLMJudgeClient, "from_env", classmethod(unavailable))

    result = await service.generate_ticket_cases(
        ticket_id=1,
        title="title",
        description="description",
        output_root=tmp_path,
    )

    assert result["status"] == "blocked"
    assert "LLM" in result["error"]


@pytest.mark.asyncio
async def test_generate_ticket_cases_with_fake_llm(tmp_path, monkeypatch):
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        async def complete(self, system_prompt, user_prompt, max_tokens=1024):
            self.calls += 1
            if self.calls == 1:
                return "# 工单分析\n\n- 功能点：创建工单"
            return json.dumps(
                [
                    {
                        "id": "TC001",
                        "title": "正常创建工单",
                        "type": "positive",
                        "priority": "P0",
                        "precondition": "用户已登录",
                        "steps": [
                            {
                                "id": 1,
                                "step": "提交工单",
                                "testData": "{}",
                                "expectedResult": "创建成功",
                            }
                        ],
                    }
                ],
                ensure_ascii=False,
            )

    result = await service.generate_ticket_cases(
        ticket_id=837,
        title="自动化提单",
        description="验证创建工单",
        output_root=tmp_path,
        llm=FakeLLM(),
    )

    assert result["status"] == "generated"
    assert result["case_count"] == 1
    assert (tmp_path / "837" / "cases.json").is_file()

def test_diagnose_run_service(tmp_path):
    from automation.src.client import OpenRobotTestClient
    from automation.mcp_server import service

    client = OpenRobotTestClient(automation_root=tmp_path, run_root=tmp_path / "runs")
    context = client.manager.create_run("diagnostic")
    client.store.record_step(context.run_id, {"step": "test", "status": "failed", "error": "Connection refused"})
    client.store.finish_run(context.run_id, "failed", exit_code=1)

    result = service.diagnose_run(context.run_id, client=client)

    assert result["failure_class"] == "environment_unreachable"
    assert result["next_steps"]