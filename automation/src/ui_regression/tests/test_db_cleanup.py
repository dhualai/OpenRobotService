"""Tests for strict database compensation cleanup."""

from __future__ import annotations

import pytest

from automation.src.ui_regression.db_cleanup import (
    CHILD_TABLES,
    DatabaseCleanup,
    DatabaseCleanupConfig,
    DatabaseCleanupFilter,
    build_task_conditions,
)


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executions: list[tuple[str, list]] = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.executions.append((sql, list(params)))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=None):
        self.cursor_instance = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class FakeDatabaseCleanup(DatabaseCleanup):
    def __init__(self, config, connection):
        super().__init__(config)
        self.connection = connection

    def _connect(self):
        return self.connection


def _config():
    return DatabaseCleanupConfig(
        host="127.0.0.1",
        port=19402,
        user="automation_cleanup",
        password="secret",
        database="helpdesk_test",
    )


def test_config_rejects_non_test_database():
    with pytest.raises(ValueError, match="unsafe database"):
        DatabaseCleanupConfig(
            host="127.0.0.1",
            port=3306,
            user="cleanup",
            password="secret",
            database="helpdesk",
        )


def test_filter_requires_exact_selector():
    with pytest.raises(ValueError, match="ticket_id or title_prefix"):
        build_task_conditions(
            DatabaseCleanupFilter(project_id="Leo_test")
        )


def test_build_task_conditions_uses_all_safety_filters():
    where_sql, params = build_task_conditions(
        DatabaseCleanupFilter(
            ticket_id=830,
            title_prefix="自动化链路验证-%",
            project_id="Leo_test",
            created_by="user-1",
            max_age_hours=24,
        )
    )

    assert "project_id = %s" in where_sql
    assert "id = %s" in where_sql
    assert "title LIKE %s" in where_sql
    assert "created_by = %s" in where_sql
    assert params == [
        "Leo_test",
        830,
        "自动化链路验证-%",
        "user-1",
        24,
    ]


def test_execute_deletes_child_tables_before_tasks():
    connection = FakeConnection(
        rows=[
            {
                "id": 830,
                "title": "自动化链路验证-test",
                "status": "closed",
                "project_id": "Leo_test",
                "created_by": "user-1",
                "created_at": "2026-09-19T12:00:00",
            }
        ]
    )
    cleanup = FakeDatabaseCleanup(_config(), connection)

    result = cleanup.cleanup(
        DatabaseCleanupFilter(
            ticket_id=830,
            title_prefix="自动化链路验证-%",
            project_id="Leo_test",
        ),
        execute=True,
    )

    executed_sql = [
        sql for sql, _ in connection.cursor_instance.executions
    ]
    child_positions = [
        next(index for index, sql in enumerate(executed_sql) if table in sql)
        for table in CHILD_TABLES
    ]
    task_delete_position = next(
        index
        for index, sql in enumerate(executed_sql)
        if sql.startswith("DELETE FROM tasks")
    )

    assert result.matched_count == 1
    assert result.deleted_rows["tasks"] == 1
    assert max(child_positions) < task_delete_position
    assert connection.commits == 1


def test_dry_run_rolls_back_without_delete():
    connection = FakeConnection(
        rows=[
            {
                "id": 830,
                "title": "自动化链路验证-test",
                "status": "closed",
                "project_id": "Leo_test",
                "created_by": "user-1",
                "created_at": "2026-09-19T12:00:00",
            }
        ]
    )
    cleanup = FakeDatabaseCleanup(_config(), connection)

    result = cleanup.cleanup(
        DatabaseCleanupFilter(
            ticket_id=830,
            title_prefix="自动化链路验证-%",
            project_id="Leo_test",
        )
    )

    assert result.matched_count == 1
    assert result.deleted_rows == {}
    assert connection.rollbacks == 1
    assert all(
        not sql.startswith("DELETE")
        for sql, _ in connection.cursor_instance.executions
    )
