"""Tests for the UI regression SSH preflight health checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "cli-check-ui-regression-ssh.py"
)
SPEC = importlib.util.spec_from_file_location(
    "cli_check_ui_regression_ssh",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class FakeResponse:
    def raise_for_status(self) -> None:
        return


class FakeSocket:
    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return


def test_check_http_retries_until_success(monkeypatch, capsys):
    calls: list[dict] = []

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        if len(calls) < 3:
            raise TimeoutError("temporary timeout")
        return FakeResponse()

    monkeypatch.setattr(cli.httpx, "get", fake_get)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    cli._check_http(
        "http://127.0.0.1:19411/health",
        label="Automation AI health",
        attempts=3,
        timeout=1.0,
        retry_delay=0.0,
    )

    assert len(calls) == 3
    assert all(call["trust_env"] is False for call in calls)
    assert "attempt 1/3" in capsys.readouterr().out


def test_check_http_reports_last_error_after_retries(monkeypatch):
    monkeypatch.setattr(
        cli.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("still timing out")
        ),
    )
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        RuntimeError,
        match="Automation AI health failed after 2 attempts: still timing out",
    ):
        cli._check_http(
            "http://127.0.0.1:19411/health",
            label="Automation AI health",
            attempts=2,
            timeout=1.0,
            retry_delay=0.0,
        )


def test_check_tcp_retries_until_success(monkeypatch):
    calls = 0

    def fake_create_connection(_address, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 1.0
        if calls == 1:
            raise OSError("connection refused")
        return FakeSocket()

    monkeypatch.setattr(cli.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    cli._check_tcp(
        "127.0.0.1",
        19402,
        label="Database forward",
        attempts=2,
        timeout=1.0,
        retry_delay=0.0,
    )

    assert calls == 2
