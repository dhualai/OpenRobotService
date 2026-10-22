from pathlib import Path

from automation.src.ticket_pipeline.gate import (
    build_gate_plan,
    build_status_aware_gate_plan,
    evaluate_ticket_statuses,
    run_gate,
)

AUTOMATION_ROOT = Path(__file__).resolve().parents[3]


def test_build_gate_plan_prefers_ticket_test(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    plan = build_gate_plan(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=["tests/business_chain"],
    )

    assert plan["ticket_ids"] == [837]
    assert str(ticket_dir / "test_gen.py") in plan["targets"]
    assert "tests/business_chain" in plan["targets"]
    assert "pytest" in plan["command"]


def test_build_gate_plan_without_ticket_uses_regression_only(tmp_path):
    plan = build_gate_plan(
        "regular maintenance",
        promoted_root=tmp_path,
        regression_targets=["tests/tasks"],
    )

    assert plan["ticket_ids"] == []
    assert plan["targets"] == ["tests/tasks"]


def test_evaluate_ticket_statuses_accepts_resolved_and_closed():
    decision = evaluate_ticket_statuses(
        [837, 838, 839],
        {837: "resolved", 838: "closed", 839: "new"},
    )

    assert decision["eligible"] == [837, 838]
    assert len(decision["rejected"]) == 1
    assert decision["rejected"][0]["ticket_id"] == 839


def test_evaluate_ticket_statuses_rejects_unknown_ticket():
    decision = evaluate_ticket_statuses([837], {})

    assert decision["eligible"] == []
    assert decision["rejected"][0]["reason"] == "ticket not found"


def test_status_aware_gate_plan_runs_functional_test_only_for_resolved(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    plan = build_status_aware_gate_plan(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=["tests/business_chain"],
        statuses={837: "resolved"},
    )

    assert plan["eligible_ticket_ids"] == [837]
    assert str(ticket_dir / "test_gen.py") in plan["targets"]
    assert "tests/business_chain" in plan["targets"]


def test_status_aware_gate_plan_skips_functional_test_for_unresolved(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    plan = build_status_aware_gate_plan(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=["tests/business_chain"],
        statuses={837: "in_progress"},
    )

    assert plan["eligible_ticket_ids"] == []
    assert str(ticket_dir / "test_gen.py") not in plan["targets"]
    assert plan["targets"] == ["tests/business_chain"]
    assert plan["rejected_tickets"][0]["ticket_id"] == 837


def test_status_aware_gate_plan_without_statuses_keeps_legacy_behaviour(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    plan = build_status_aware_gate_plan(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=["tests/business_chain"],
        statuses=None,
    )

    assert str(ticket_dir / "test_gen.py") in plan["targets"]
    assert "eligible_ticket_ids" not in plan


def test_status_aware_gate_plan_regression_runs_without_any_ticket(tmp_path):
    plan = build_status_aware_gate_plan(
        "regular maintenance",
        promoted_root=tmp_path,
        regression_targets=["tests/tasks"],
        statuses={},
    )

    assert plan["targets"] == ["tests/tasks"]


def test_status_aware_gate_plan_fails_closed_when_status_lookup_failed(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    plan = build_status_aware_gate_plan(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=["tests/business_chain"],
        statuses=None,
        status_check=True,
    )

    assert plan["status_lookup"] == "failed"
    assert plan["functional_targets"] == []
    assert str(ticket_dir / "test_gen.py") not in plan["targets"]
    assert plan["targets"] == ["tests/business_chain"]
    assert plan["rejected_tickets"][0]["reason"] == "ticket status lookup failed"


def test_status_aware_gate_plan_splits_functional_and_regression_targets(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    plan = build_status_aware_gate_plan(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=["tests/business_chain", "tests/tasks"],
        statuses={837: "resolved"},
    )

    assert plan["functional_targets"] == [str(ticket_dir / "test_gen.py")]
    assert plan["regression_targets"] == ["tests/business_chain", "tests/tasks"]
    assert plan["targets"] == [
        str(ticket_dir / "test_gen.py"),
        "tests/business_chain",
        "tests/tasks",
    ]


def _write_gate_fixtures(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text(
        "def test_functional_fails():\n    assert False\n", encoding="utf-8"
    )
    regression = tmp_path / "test_regression.py"
    regression.write_text("def test_regression_ok():\n    assert True\n", encoding="utf-8")
    return ticket_dir, regression


def test_run_gate_does_not_block_on_a_functional_failure(tmp_path):
    ticket_dir, regression = _write_gate_fixtures(tmp_path)

    result = run_gate(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=[str(regression)],
        cwd=AUTOMATION_ROOT,
        statuses={837: "resolved"},
    )

    assert result["functional_failed"] is True
    assert result["regression_failed"] is False
    assert result["blocking"] is False
    assert result["passed"] is True


def test_run_gate_blocks_when_the_functional_failure_is_configured_to_block(tmp_path):
    ticket_dir, regression = _write_gate_fixtures(tmp_path)

    result = run_gate(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=[str(regression)],
        cwd=AUTOMATION_ROOT,
        statuses={837: "resolved"},
        block_on_functional_failure=True,
    )

    assert result["functional_failed"] is True
    assert result["blocking"] is True
    assert result["passed"] is False


def test_run_gate_blocks_on_a_regression_failure(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text(
        "def test_functional_ok():\n    assert True\n", encoding="utf-8"
    )
    regression = tmp_path / "test_regression.py"
    regression.write_text(
        "def test_regression_fails():\n    assert False\n", encoding="utf-8"
    )

    result = run_gate(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=[str(regression)],
        cwd=AUTOMATION_ROOT,
        statuses={837: "resolved"},
    )

    assert result["functional_failed"] is False
    assert result["regression_failed"] is True
    assert result["blocking"] is True
    assert result["passed"] is False


def test_run_gate_applies_per_group_backend_environment(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text(
        "import os\n\n\ndef test_functional_backend():\n"
        "    assert os.getenv('USE_MOCK') == '0'\n",
        encoding="utf-8",
    )
    regression = tmp_path / "test_regression_env.py"
    regression.write_text(
        "import os\n\n\ndef test_regression_backend():\n"
        "    assert os.getenv('USE_MOCK') == '1'\n",
        encoding="utf-8",
    )

    result = run_gate(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=[str(regression)],
        cwd=AUTOMATION_ROOT,
        statuses={837: "resolved"},
        functional_env={"USE_MOCK": "0"},
        regression_env={"USE_MOCK": "1"},
    )

    assert result["functional"]["passed"] is True
    assert result["regression"]["passed"] is True


def test_run_gate_skips_functional_tests_when_the_status_lookup_failed(tmp_path):
    ticket_dir, regression = _write_gate_fixtures(tmp_path)

    result = run_gate(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=[str(regression)],
        cwd=AUTOMATION_ROOT,
        statuses=None,
        status_check=True,
    )

    assert result["status_lookup"] == "failed"
    assert result["functional"]["executed"] is False
    assert result["functional_failed"] is False
    assert result["passed"] is True