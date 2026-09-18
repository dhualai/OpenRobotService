from automation.src.ticket_pipeline import extract_ticket_ids, select_regression_targets


def test_extract_ticket_ids_supports_common_formats():
    text = "fix ORS-837 and closes #838\n工单:839\nticket_id: 840"
    assert extract_ticket_ids(text) == [837, 838, 839, 840]


def test_select_regression_targets_prefers_ticket_tests(tmp_path):
    ticket_dir = tmp_path / "837"
    ticket_dir.mkdir()
    (ticket_dir / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    selected = select_regression_targets(
        "fix ORS-837",
        promoted_root=tmp_path,
        regression_targets=["tests/business_chain", "tests/tasks"],
    )

    assert selected[0] == str(ticket_dir / "test_gen.py")
    assert selected[1:] == ["tests/business_chain", "tests/tasks"]