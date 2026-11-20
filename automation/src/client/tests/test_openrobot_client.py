import subprocess

import pytest

from automation.src.client import OpenRobotTestClient


class FakeProcess:
    pid = 4242
    returncode = 0

    def __init__(self, *args, **kwargs):
        pass

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_client_capabilities_and_health(tmp_path):
    client = OpenRobotTestClient(automation_root=tmp_path, run_root=tmp_path / "runs")

    capabilities = client.capabilities()
    names = {scenario["name"] for scenario in capabilities["scenarios"]}
    assert {"framework", "api_mock", "business_chain", "real_smoke", "ai_eval"} <= names
    assert client.health()["status"] == "healthy"


def test_client_rejects_unknown_scenario(tmp_path):
    client = OpenRobotTestClient(automation_root=tmp_path, run_root=tmp_path / "runs")

    with pytest.raises(ValueError, match="Unknown scenario"):
        client.get_scenario("missing")


def test_client_submit_records_terminal_status(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    client = OpenRobotTestClient(automation_root=tmp_path, run_root=tmp_path / "runs")

    submitted = client.submit("framework", profile="fast", env="local")
    run = client.get_run(submitted["run_id"])

    assert run["run"]["scenario"] == "framework"
    assert run["run"]["command"]
    assert run["status"]["status"] == "passed"
    assert run["status"]["exit_code"] == 0