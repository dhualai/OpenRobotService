"""Infrastructure contract checks (MySQL / Redis / Qdrant).

These tests are marked db and are skipped automatically when the service
is unavailable, so the fast lane stays independent of external services.
"""

import pytest

from automation.src.assertions import assert_not_empty, assert_table_has_columns

pytestmark = [pytest.mark.db]


@pytest.mark.db
def test_mysql_tasks_table_exists(mysql_client):
    row = mysql_client.fetch_one("SELECT COUNT(*) AS total FROM tasks")
    assert row is not None
    assert "total" in row


@pytest.mark.db
def test_mysql_tasks_required_columns(mysql_client):
    columns = assert_table_has_columns(
        mysql_client,
        "tasks",
        {"id", "title", "description", "status", "created_by", "project_id", "resolved_at", "closed_at"},
    )
    assert_not_empty(columns, "tasks table has no columns")


@pytest.mark.db
def test_mysql_task_comments_required_columns(mysql_client):
    assert_table_has_columns(mysql_client, "task_comments", {"id", "task_id", "content", "created_by"})


@pytest.mark.db
def test_redis_set_get_delete(redis_client):
    key = "automation:health:contract"
    assert redis_client.set(key, "ok", ex=60) is True
    try:
        assert redis_client.get(key) == "ok"
    finally:
        redis_client.delete(key)


@pytest.mark.db
def test_qdrant_reachable(qdrant_client):
    assert qdrant_client.is_connected is True