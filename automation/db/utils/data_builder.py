"""Test data builders for database validation."""

from typing import Any, Dict, List, Optional
from automation.clients.mysql_client import MySQLClient
from automation.clients.redis_client import RedisClient
from automation.clients.qdrant_client import QdrantClient


class MySQLDataBuilder:
    """Build and insert test data into MySQL."""

    def __init__(self, client: MySQLClient):
        self._client = client

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        """Insert a row and return affected rows."""
        cols = ", ".join(data.keys())
        ph = ", ".join(["%s"] * len(data))
        return self._client.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({ph})",
            tuple(data.values()),
        )

    def insert_batch(self, table: str, rows: List[Dict[str, Any]]) -> int:
        """Insert multiple rows."""
        total = 0
        for row in rows:
            total += self.insert(table, row)
        return total


class RedisDataBuilder:
    """Prepare Redis cache state for testing."""

    def __init__(self, client: RedisClient):
        self._client = client

    def set_key(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        return self._client.set(key, value, ex=ex)

    def delete_key(self, key: str) -> bool:
        return self._client.delete(key)


class QdrantDataBuilder:
    """Prepare Qdrant vector data for testing."""

    def __init__(self, client: QdrantClient):
        self._client = client

    def insert_points(self, collection: str, points: List[Dict[str, Any]]) -> int:
        return self._client.upsert(collection, points)
