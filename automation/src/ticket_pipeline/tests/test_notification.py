"""Unit tests for the ticket gate notification payloads."""

import json

from automation.src.ticket_pipeline.notification import (
    build_notification,
    emit_github_annotations,
    extract_failed_tests,
    post_ticket_comment,
    render_comment,
    write_notification,
)

FAILED_OUTPUT = """=========================== short test summary info ===========================
FAILED ../promoted-cases/837/test_gen.py::TestTicket837::test_tc001 - AssertionError
ERROR ../promoted-cases/837/test_gen.py::TestTicket837::test_tc002 - RuntimeError
=========================== 1 failed, 1 error in 3.10s ============================
"""


def test_extract_failed_tests_reads_pytest_output():
    assert extract_failed_tests(FAILED_OUTPUT) == [
        "../promoted-cases/837/test_gen.py::TestTicket837::test_tc001",
        "../promoted-cases/837/test_gen.py::TestTicket837::test_tc002",
    ]


def test_extract_failed_tests_on_empty_output():
    assert extract_failed_tests("") == []


def test_build_notification_describes_the_failure():
    notification = build_notification(
        ticket_ids=[837],
        functional={"stdout": FAILED_OUTPUT, "stderr": "", "returncode": 1},
        status_lookup="ok",
    )

    assert notification["status"] == "functional_failed"
    assert notification["ticket_ids"] == [837]
    assert notification["ticket_refs"] == "ORS-837"
    assert len(notification["failed_tests"]) == 2
    assert "只通知不阻断" in notification["summary"]


def test_render_comment_contains_the_failed_cases():
    notification = build_notification(
        ticket_ids=[837],
        functional={"stdout": FAILED_OUTPUT, "stderr": "", "returncode": 1},
    )

    comment = render_comment(notification)

    assert "自动化功能用例未通过" in comment
    assert "test_tc001" in comment
    assert "ORS-837" in comment


def test_emit_github_annotations_prints_warnings(capsys):
    notification = build_notification(
        ticket_ids=[837],
        functional={"stdout": FAILED_OUTPUT, "stderr": "", "returncode": 1},
    )

    emit_github_annotations(notification)

    printed = capsys.readouterr().out
    assert "::warning::" in printed
    assert "test_tc001" in printed


def test_write_notification_persists_json(tmp_path):
    notification = build_notification(
        ticket_ids=[837],
        functional={"stdout": FAILED_OUTPUT, "stderr": "", "returncode": 1},
    )
    target = tmp_path / "output" / "notification.json"

    write_notification(target, notification)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["ticket_ids"] == [837]


def test_post_ticket_comment_skips_without_credentials(monkeypatch):
    monkeypatch.delenv("AUTOMATION_BOT_USERNAME", raising=False)
    monkeypatch.delenv("AUTOMATION_BOT_PASSWORD", raising=False)

    result = post_ticket_comment("http://localhost:8400", 837, "hello")

    assert result["status"] == "skipped"
    assert "AUTOMATION_BOT_USERNAME" in result["reason"]


def test_post_ticket_comment_reports_a_login_failure(monkeypatch):
    monkeypatch.setenv("AUTOMATION_BOT_USERNAME", "bot")
    monkeypatch.setenv("AUTOMATION_BOT_PASSWORD", "secret")

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)

    result = post_ticket_comment("http://localhost:8400", 837, "hello")

    assert result["status"] == "failed"
    assert "login failed" in result["reason"]
