"""Tests for network capture redaction and evidence."""

from __future__ import annotations

from automation.src.ui_regression.capture import (
    REDACTED,
    CapturedExchange,
    CapturedStep,
    build_step_evidence,
    redact_url,
    redact_value,
)


def test_redact_value_removes_credentials_and_bearer_tokens():
    payload = {
        "username": "u1_auto",
        "password": "123456",
        "access_token": "secret-token",
        "headers": {
            "Authorization": "Bearer abc.def.ghi",
            "Cookie": "sid=secret",
        },
        "items": [{"refresh_token": "refresh-secret"}],
    }

    redacted = redact_value(payload)

    assert redacted["username"] == "u1_auto"
    assert redacted["password"] == REDACTED
    assert redacted["access_token"] == REDACTED
    assert redacted["headers"]["Cookie"] == REDACTED
    assert redacted["items"][0]["refresh_token"] == REDACTED


def test_redact_url_removes_sensitive_query_parameters():
    value = redact_url(
        "http://127.0.0.1/api/tasks/1/ws?token=secret&mode=debug&access_token=hidden"
    )

    assert value == (
        "http://127.0.0.1/api/tasks/1/ws"
        f"?token={REDACTED}&mode=debug&access_token={REDACTED}"
    )


def test_build_step_evidence_summarizes_assertion_and_interfaces():
    captured = CapturedStep(
        step_id="S10",
        role="U2",
        action="确认接单",
        exchanges=[
            CapturedExchange(
                sequence=1,
                method="POST",
                url="http://127.0.0.1/api/tasks/1/respond",
                status=200,
            ),
            CapturedExchange(
                sequence=2,
                method="GET",
                url="http://127.0.0.1/api/tasks/1",
                status=None,
            ),
        ],
    )

    evidence = build_step_evidence(captured)

    assert evidence["assertion"]["result"] == "通过"
    assert evidence["interfaces"][0]["result"] == "通过"
    assert evidence["interfaces"][0]["path"] == "/api/tasks/1/respond"
    assert evidence["interfaces"][1]["result"] == "未响应"
