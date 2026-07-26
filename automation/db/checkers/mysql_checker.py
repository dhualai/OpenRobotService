"""MySQL data checker: verify database state matches expectations."""

from typing import Any, Dict, Optional

from automation.clients.mysql_client import MySQLClient


class MySQLChecker:
    """Assert MySQL database state using MySQLClient.

    Usage:
        checker = MySQLChecker(client)
        row = checker.assert_row_exists("tickets", id=1001)
        count = checker.assert_row_count("tickets", exact=5)
    """

    def __init__(self, client: MySQLClient):
        self._client = client

    def assert_row_exists(self, table: str, **filters: Any) -> Dict[str, Any]:
        """Assert a row exists matching filters, return it."""
        if not filters:
            row = self._client.fetch_one(f"SELECT * FROM {table} LIMIT 1")
        else:
            clauses = [f"{k}=%s" for k in filters]
            row = self._client.fetch_one(
                f"SELECT * FROM {table} WHERE {' AND '.join(clauses)}",
                tuple(filters.values()),
            )
        assert row is not None, f"Row in '{table}' with {filters} not found"
        return row

    def assert_row_not_exists(self, table: str, **filters: Any) -> None:
        """Assert no row matches filters."""
        clauses = [f"{k}=%s" for k in filters]
        row = self._client.fetch_one(
            f"SELECT * FROM {table} WHERE {' AND '.join(clauses)}",
            tuple(filters.values()),
        )
        assert row is None, f"Unexpected row in '{table}' with {filters}: {row}"

    def assert_row_count(self, table: str, *,
                         exact: Optional[int] = None,
                         min: Optional[int] = None,
                         max: Optional[int] = None) -> int:
        """Assert row count constraints, return actual count."""
        result = self._client.fetch_one(f"SELECT COUNT(*) as cnt FROM {table}")
        count = result["cnt"] if result else 0
        if exact is not None:
            assert count == exact, f"Expected {exact} rows in '{table}', got {count}"
        if min is not None:
            assert count >= min, f"Expected >= {min} rows in '{table}', got {count}"
        if max is not None:
            assert count <= max, f"Expected <= {max} rows in '{table}', got {count}"
        return count

    def assert_matches(self, table: str, expected: Dict[str, Any]) -> Dict[str, Any]:
        """Assert a row matches expected values (subset comparison)."""
        filters = {k: v for k, v in expected.items()
                   if k not in ("id", "created_at", "updated_at")}
        row = self.assert_row_exists(table, **filters)
        for key, val in expected.items():
            if key in row:
                assert row[key] == val, \
                    f"'{table}' field '{key}': expected {val!r}, got {row[key]!r}"
        return row

    def assert_column_values(self, table: str, column: str,
                             expected_values: set) -> None:
        """Assert column contains only expected values."""
        rows = self._client.fetch_all(f"SELECT DISTINCT {column} FROM {table}")
        actual = {row[column] for row in rows}
        unexpected = actual - expected_values
        assert not unexpected, \
            f"Unexpected values in '{table}.{column}': {unexpected}"
