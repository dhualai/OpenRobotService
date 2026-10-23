"""End-to-end test of the ticket-driven automation loop.

The scenario mirrors the closed loop: a feature ticket is scanned, cases are
generated from the API spec, the candidate is promoted after human review, and
the gate selects functional plus regression targets.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
AUTOMATION_ROOT = REPO_ROOT / "automation"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.ci_ai_gen.ticket_pipeline import TicketCaseGenerator  # noqa: E402
from automation.src.ticket_pipeline.gate import build_status_aware_gate_plan  # noqa: E402
from automation.src.ticket_pipeline.models import TicketCandidate  # noqa: E402
from automation.src.ticket_pipeline.pipeline import TicketPipeline  # noqa: E402
from automation.src.ticket_pipeline.scanner import TicketScanner  # noqa: E402

SPEC = {
    "paths": {
        "/api/tasks": {"get": {}},
        "/api/tasks/{task_id}": {"get": {}},
        "/api/auth/login": {"post": {}},
    }
}

ENDPOINTS = [
    {"method": "GET", "path": "/api/tasks", "summary": "查询工单列表"},
    {"method": "GET", "path": "/api/tasks/{task_id}", "summary": "查询工单详情"},
    {"method": "POST", "path": "/api/auth/login", "summary": "登录"},
]

CASES = json.dumps(
    [
        {
            "id": "TC001",
            "title": "登录后可以查询工单列表",
            "type": "positive",
            "module": "工单查询",
            "requests": [
                {
                    "step": "Step 1: 登录",
                    "method": "POST",
                    "path": "/api/auth/login",
                    "body": {"username": "testadmin", "password": "admin123"},
                    "expected_status": 200,
                    "auth": False,
                },
                {
                    "step": "Step 2: 查询工单列表",
                    "method": "GET",
                    "path": "/api/tasks",
                    "expected_status": 200,
                    "expected_fields": {"total": 1},
                },
            ],
        }
    ],
    ensure_ascii=False,
)


class FakeLLM:
    def __init__(self):
        self.responses = ["分析内容", CASES]

    async def complete(self, system_prompt, user_prompt, max_tokens=1024):
        return self.responses.pop(0)


class FakeSource:
    def __init__(self, tickets):
        self.tickets = tickets

    def fetch(self, limit):
        return self.tickets[:limit]


def make_ticket():
    return TicketCandidate(
        id=9001,
        title="工单列表支持按项目筛选",
        description="需求：工单列表增加项目筛选",
        task_type="feature",
        status="new",
        project_id="Leo_test",
        project_name="摇人吧服务号",
        tags=["auto_case"],
    )


@pytest.fixture
def loop(tmp_path):
    candidate_root = tmp_path / "generated-cases" / "tickets"
    promoted_root = tmp_path / "promoted-cases"
    generator = TicketCaseGenerator(
        llm=FakeLLM(),
        output_root=candidate_root,
        spec=SPEC,
        endpoints=ENDPOINTS,
    )
    scanner = TicketScanner(FakeSource([make_ticket()]), task_types=("feature",))
    pipeline = TicketPipeline(scanner, generator, promoted_root=promoted_root)
    return {
        "pipeline": pipeline,
        "candidate_root": candidate_root,
        "promoted_root": promoted_root,
    }


@pytest.mark.asyncio
async def test_loop_generates_reviewable_and_runnable_artifact(loop):
    pipeline = loop["pipeline"]
    candidate_root = loop["candidate_root"]

    result = await pipeline.run()

    assert result["generated"] == 1
    assert result["errors"] == 0

    manifest = json.loads(
        (candidate_root / "9001" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["review_status"] == "pending_review"
    assert manifest["spec_issues"] == []
    assert manifest["request_count"] == 2

    script = candidate_root / "9001" / "test_gen.py"
    assert script.is_file()
    assert "def test_tc001" in script.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_loop_is_idempotent(loop):
    pipeline = loop["pipeline"]

    await pipeline.run()
    second = await pipeline.run()

    assert second["generated"] == 0
    assert second["skipped"] == 1


@pytest.mark.asyncio
async def test_promotion_requires_human_review(loop):
    pipeline = loop["pipeline"]
    candidate_root = loop["candidate_root"]
    await pipeline.run()

    with pytest.raises(ValueError, match="not approved"):
        pipeline.promote(9001)

    manifest_path = candidate_root / "9001" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review_status"] = "approved"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    promoted = pipeline.promote(9001)

    assert promoted["status"] == "promoted"
    assert (loop["promoted_root"] / "9001" / "test_gen.py").is_file()


@pytest.mark.asyncio
async def test_gate_runs_functional_test_only_after_ticket_resolved(loop):
    pipeline = loop["pipeline"]
    promoted_root = loop["promoted_root"]
    await pipeline.run()

    manifest_path = loop["candidate_root"] / "9001" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review_status"] = "approved"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    pipeline.promote(9001)

    message = "feature（ORS-9001）：工单列表支持按项目筛选"

    resolved = build_status_aware_gate_plan(
        message,
        promoted_root=promoted_root,
        regression_targets=["tests/business_chain", "tests/tasks"],
        statuses={9001: "resolved"},
    )
    assert resolved["eligible_ticket_ids"] == [9001]
    assert str(promoted_root / "9001" / "test_gen.py") in resolved["targets"]
    assert "tests/business_chain" in resolved["targets"]

    unresolved = build_status_aware_gate_plan(
        message,
        promoted_root=promoted_root,
        regression_targets=["tests/business_chain", "tests/tasks"],
        statuses={9001: "in_progress"},
    )
    assert unresolved["eligible_ticket_ids"] == []
    assert str(promoted_root / "9001" / "test_gen.py") not in unresolved["targets"]
    assert unresolved["targets"] == ["tests/business_chain", "tests/tasks"]


@pytest.mark.asyncio
async def test_promoted_script_is_collectable_by_pytest(loop):
    pipeline = loop["pipeline"]
    candidate_root = loop["candidate_root"]
    await pipeline.run()

    manifest_path = candidate_root / "9001" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review_status"] = "approved"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    pipeline.promote(9001)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(loop["promoted_root"] / "9001" / "test_gen.py"),
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(AUTOMATION_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "test_tc001" in completed.stdout
