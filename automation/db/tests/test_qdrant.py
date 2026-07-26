"""Tests for QdrantChecker."""

import pytest

from automation.db.checkers.qdrant_checker import QdrantChecker


class TestQdrantChecker:
    @pytest.fixture
    def checker(self, mock_qdrant_client):
        return QdrantChecker(mock_qdrant_client)

    def test_assert_collection_exists(self, checker, mock_qdrant_client):
        mock_qdrant_client.collection_exists.return_value = True
        assert checker.assert_collection_exists("col")

    def test_assert_collection_not_exists(self, checker, mock_qdrant_client):
        mock_qdrant_client.collection_exists.return_value = False
        checker.assert_collection_not_exists("col")

    def test_assert_collection_not_exists_fail(self, checker, mock_qdrant_client):
        mock_qdrant_client.collection_exists.return_value = True
        with pytest.raises(AssertionError, match="unexpectedly"):
            checker.assert_collection_not_exists("col")

    def test_assert_search_returns(self, checker, mock_qdrant_client):
        mock_qdrant_client.search.return_value = [
            {"id": 1, "score": 0.9, "payload": {}},
            {"id": 2, "score": 0.8, "payload": {}},
        ]
        results = checker.assert_search_returns("col", [0.1, 0.2], expected_ids=[1, 2])
        assert len(results) == 2

    def test_assert_search_returns_missing(self, checker, mock_qdrant_client):
        mock_qdrant_client.search.return_value = [{"id": 1, "score": 0.9, "payload": {}}]
        with pytest.raises(AssertionError, match="not in search results"):
            checker.assert_search_returns("col", [0.1], expected_ids=[99])

    def test_assert_point_count(self, checker, mock_qdrant_client):
        mock_qdrant_client.search.return_value = [{"id": i} for i in range(5)]
        assert checker.assert_point_count("col", 5) == 5

    def test_assert_point_count_wrong(self, checker, mock_qdrant_client):
        mock_qdrant_client.search.return_value = [{"id": i} for i in range(3)]
        with pytest.raises(AssertionError, match="Expected 5"):
            checker.assert_point_count("col", 5)
