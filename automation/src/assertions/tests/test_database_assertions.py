import pytest

from automation.src.assertions.database import (
    assert_table_has_columns,
    assert_task_row,
    assert_task_status,
)


class FakeMySQL:
    def __init__(self, row):
        self.row = row
        self.columns = [{"Field": "id"}, {"Field": "title"}, {"Field": "status"}]

    def fetch_one(self, query, params=None):
        return self.row

    def fetch_all(self, query, params=None):
        return self.columns


def test_assert_task_row_success():
    client = FakeMySQL({"id": 7, "title": "T", "status": "new"})
    row = assert_task_row(client, 7, {"title": "T", "status": "new"})
    assert row["id"] == 7


def test_assert_task_status_failure_contains_details():
    client = FakeMySQL({"id": 7, "title": "T", "status": "new"})
    with pytest.raises(BaseException, match="mismatch"):
        assert_task_status(client, 7, "closed")


def test_assert_table_has_columns_success():
    client = FakeMySQL({})
    columns = assert_table_has_columns(client, "tasks", {"id", "title"})
    assert {"id", "title", "status"} == columns


def test_assert_table_rejects_unsafe_name():
    with pytest.raises(ValueError, match="Unsafe table name"):
        assert_table_has_columns(FakeMySQL({}), "tasks; DROP TABLE tasks", {"id"})