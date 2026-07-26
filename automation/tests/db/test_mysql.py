"""Tests for MySQLChecker."""

import pytest

from automation.db.checkers.mysql_checker import MySQLChecker


class TestMySQLChecker:
    @pytest.fixture
    def checker(self, mock_mysql_client):
        return MySQLChecker(mock_mysql_client)

    def test_assert_row_exists_found(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = {"id": 1, "name": "test"}
        row = checker.assert_row_exists("users", name="test")
        assert row["id"] == 1
        mock_mysql_client.fetch_one.assert_called_once()

    def test_assert_row_exists_not_found(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = None
        with pytest.raises(AssertionError, match="not found"):
            checker.assert_row_exists("users", name="missing")

    def test_assert_row_not_exists(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = None
        checker.assert_row_not_exists("users", name="missing")

    def test_assert_row_not_exists_fail(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = {"id": 1}
        with pytest.raises(AssertionError, match="Unexpected"):
            checker.assert_row_not_exists("users", name="existing")

    def test_assert_row_count_exact_pass(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = {"cnt": 5}
        assert checker.assert_row_count("tickets", exact=5) == 5

    def test_assert_row_count_exact_fail(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = {"cnt": 3}
        with pytest.raises(AssertionError, match="Expected 5"):
            checker.assert_row_count("tickets", exact=5)

    def test_assert_row_count_min(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = {"cnt": 10}
        assert checker.assert_row_count("tickets", min=5) == 10

    def test_assert_row_count_max(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_one.return_value = {"cnt": 3}
        assert checker.assert_row_count("tickets", max=5) == 3

    def test_assert_matches(self, checker, mock_mysql_client):
        expected = {"name": "test", "role": "admin"}
        mock_mysql_client.fetch_one.return_value = {"id": 1, "name": "test", "role": "admin"}
        row = checker.assert_matches("users", expected)
        assert row["id"] == 1

    def test_assert_column_values(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_all.return_value = [
            {"status": "pending"}, {"status": "resolved"},
        ]
        checker.assert_column_values("tickets", "status", {"pending", "resolved", "closed"})

    def test_assert_column_values_unexpected(self, checker, mock_mysql_client):
        mock_mysql_client.fetch_all.return_value = [
            {"status": "pending"}, {"status": "unknown_status"},
        ]
        with pytest.raises(AssertionError, match="Unexpected"):
            checker.assert_column_values("tickets", "status", {"pending", "resolved"})
