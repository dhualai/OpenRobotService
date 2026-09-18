from automation.src.ticket_pipeline.gate import build_gate_plan


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