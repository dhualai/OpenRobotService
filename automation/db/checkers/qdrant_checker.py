"""Qdrant data checker: verify vector search state matches expectations."""

from typing import Any, Dict, List, Optional

from automation.clients.qdrant_client import QdrantClient


class QdrantChecker:
    """Assert Qdrant vector database state using QdrantClient.

    Usage:
        checker = QdrantChecker(client)
        checker.assert_collection_exists("tasks")
        checker.assert_search_returns("tasks", [0.1, 0.2], expected_ids=[1, 2])
    """

    def __init__(self, client: QdrantClient):
        self._client = client

    def assert_collection_exists(self, name: str) -> bool:
        """Assert collection exists."""
        assert self._client.collection_exists(name), \
            f"Qdrant collection '{name}' does not exist"
        return True

    def assert_collection_not_exists(self, name: str) -> None:
        """Assert collection does not exist."""
        assert not self._client.collection_exists(name), \
            f"Qdrant collection '{name}' unexpectedly exists"

    def assert_search_returns(self, collection_name: str,
                               query_vector: List[float],
                               expected_ids: List[Any],
                               limit: int = 10) -> List[dict]:
        """Assert search returns expected point IDs."""
        results = self._client.search(collection_name, query_vector, limit=limit)
        actual_ids = [r["id"] for r in results]
        for eid in expected_ids:
            assert eid in actual_ids, \
                f"Expected point id {eid} not in search results"
        return results

    def assert_point_count(self, collection_name: str,
                           expected: int) -> int:
        """Assert total points in collection matches expected count."""
        results = self._client.search(collection_name, [0.0] * 384, limit=10000)
        count = len(results)
        assert count == expected, \
            f"Expected {expected} points in '{collection_name}', got {count}"
        return count
