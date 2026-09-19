"""Tests for best-effort test-data cleanup."""

from __future__ import annotations

import json

import httpx

from automation.src.ui_regression.cleanup import CleanupManager
from automation.src.ui_regression.db_cleanup import (
    DatabaseCleanupConfig,
    DatabaseCleanupResult,
)


class FakeDatabaseCleanup:
    def __init__(self, result: DatabaseCleanupResult | None = None):
        self.config = DatabaseCleanupConfig(
            host="127.0.0.1",
            port=19402,
            user="cleanup",
            password="secret",
            database="helpdesk_test",
        )
        self.result = result or DatabaseCleanupResult()
        self.filters = []

    def cleanup(self, cleanup_filter, execute=False):
        self.filters.append((cleanup_filter, execute))
        return self.result


def test_cleanup_deletes_ticket_and_conversation():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/auth/login":
            username = json.loads(request.content)["username"]
            return httpx.Response(200, json={"access_token": f"token-{username}"})
        if request.url.path == "/api/tasks/837":
            assert request.headers["authorization"] == "Bearer token-admin"
            return httpx.Response(204)
        if request.url.path == "/api/call/conversations/11":
            assert request.headers["authorization"] == "Bearer token-u1_auto"
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.Client(
        base_url="http://backend.test",
        transport=httpx.MockTransport(handler),
    )
    manager = CleanupManager(
        "http://backend.test",
        admin_username="admin",
        admin_password="admin-pass",
        u1_username="u1_auto",
        u1_password="u1-pass",
        client=client,
    )

    result = manager.cleanup(ticket_id=837, conversation_id=11)

    assert result.ok is True
    assert result.deleted == ["工单", "会话"]
    assert ("DELETE", "/api/tasks/837") in calls
    assert ("DELETE", "/api/call/conversations/11") in calls


def test_cleanup_failure_returns_warning_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(500, text="boom")

    client = httpx.Client(
        base_url="http://backend.test",
        transport=httpx.MockTransport(handler),
    )
    manager = CleanupManager(
        "http://backend.test",
        admin_username="admin",
        admin_password="admin-pass",
        client=client,
    )

    result = manager.cleanup(ticket_id=837)

    assert result.ok is False
    assert result.deleted == []
    assert result.warnings[0].resource == "工单"
    assert "HTTP 500" in result.warnings[0].message


def test_cleanup_uses_database_fallback_after_api_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "token"})
        if request.url.path == "/api/tasks/837":
            return httpx.Response(500, text="foreign key")
        return httpx.Response(404)

    client = httpx.Client(
        base_url="http://backend.test",
        transport=httpx.MockTransport(handler),
    )
    db_cleanup = FakeDatabaseCleanup(
        DatabaseCleanupResult(
            matched_tasks=[{"id": 837}],
            deleted_rows={"task_comments": 2, "tasks": 1},
        )
    )
    manager = CleanupManager(
        "http://backend.test",
        admin_username="admin",
        admin_password="admin-pass",
        db_cleanup=db_cleanup,
        client=client,
    )

    result = manager.cleanup(ticket_id=837)

    assert result.ok is True
    assert result.deleted == ["工单(数据库补偿)"]
    assert result.database_deleted_rows["tasks"] == 1
    assert db_cleanup.filters[0][0].ticket_id == 837
    assert db_cleanup.filters[0][1] is True


def test_database_fallback_failure_is_a_warning():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(500, text="foreign key")

    client = httpx.Client(
        base_url="http://backend.test",
        transport=httpx.MockTransport(handler),
    )
    manager = CleanupManager(
        "http://backend.test",
        admin_username="admin",
        admin_password="admin-pass",
        db_cleanup=FakeDatabaseCleanup(),
        client=client,
    )

    result = manager.cleanup(ticket_id=837)

    assert result.ok is False
    assert any(
        "数据库补偿" in warning.message
        for warning in result.warnings
    )


def test_cleanup_missing_credentials_is_a_warning():
    manager = CleanupManager("http://backend.test")
    result = manager.cleanup(ticket_id=837, conversation_id=11)

    assert result.ok is False
    assert [warning.resource for warning in result.warnings] == ["工单", "会话"]
