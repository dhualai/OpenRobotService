"""Database assertion helpers for business-chain tests."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

import pytest

from automation.src.assertions.report import record


def fetch_task(mysql_client, task_id: int) -> Optional[Dict[str, Any]]:
    """Fetch one task row by primary key."""
    return mysql_client.fetch_one(
        "SELECT id, title, description, status, created_by, assigned_to, "
        "project_id, project_name, created_at, resolved_at, closed_at "
        "FROM tasks WHERE id = %s",
        (task_id,),
    )


def assert_task_row(mysql_client, task_id: int, expected: Dict[str, Any]) -> Dict[str, Any]:
    """Assert that a task row exists and contains the expected field values."""
    row = fetch_task(mysql_client, task_id)
    if row is None:
        pytest.fail(f"Task {task_id} not found in MySQL")
    for field, expected_value in expected.items():
        actual_value = row.get(field)
        try:
            record(
                {
                    "断言": f"tasks[{task_id}].{field}",
                    "期望值": expected_value,
                    "实际值": actual_value,
                }
            )
        except Exception:
            pass
        if actual_value != expected_value:
            pytest.fail(
                f"Task {task_id} field {field!r} mismatch: "
                f"expected {expected_value!r}, got {actual_value!r}; row={row}"
            )
    return row


def assert_task_status(mysql_client, task_id: int, status: str) -> Dict[str, Any]:
    """Assert the persisted task status."""
    return assert_task_row(mysql_client, task_id, {"status": status})


def assert_table_has_columns(mysql_client, table: str, required: Iterable[str]) -> set[str]:
    """Assert that a table exposes all required columns."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", table):
        raise ValueError(f"Unsafe table name: {table!r}")
    rows = mysql_client.fetch_all(f"SHOW COLUMNS FROM {table}")
    columns = {row["Field"] for row in rows}
    missing = set(required) - columns
    if missing:
        pytest.fail(f"Table {table} is missing columns: {sorted(missing)}; columns={sorted(columns)}")
    return columns