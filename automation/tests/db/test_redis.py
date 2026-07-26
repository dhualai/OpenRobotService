"""Tests for RedisChecker."""

import pytest

from automation.db.checkers.redis_checker import RedisChecker


class TestRedisChecker:
    @pytest.fixture
    def checker(self, mock_redis_client):
        return RedisChecker(mock_redis_client)

    def test_assert_key_exists(self, checker, mock_redis_client):
        mock_redis_client.get.return_value = "val123"
        assert checker.assert_key_exists("k") == "val123"

    def test_assert_key_not_exists(self, checker, mock_redis_client):
        mock_redis_client.exists.return_value = False
        checker.assert_key_not_exists("k")

    def test_assert_key_not_exists_fail(self, checker, mock_redis_client):
        mock_redis_client.exists.return_value = True
        with pytest.raises(AssertionError, match="unexpectedly exists"):
            checker.assert_key_not_exists("k")

    def test_assert_value_equals_pass(self, checker, mock_redis_client):
        mock_redis_client.get.return_value = "expected_val"
        checker.assert_value_equals("k", "expected_val")

    def test_assert_value_equals_fail(self, checker, mock_redis_client):
        mock_redis_client.get.return_value = "wrong_val"
        with pytest.raises(AssertionError, match="expected"):
            checker.assert_value_equals("k", "expected_val")

    def test_assert_value_contains_pass(self, checker, mock_redis_client):
        mock_redis_client.get.return_value = "hello world from test"
        checker.assert_value_contains("k", "world")

    def test_assert_value_contains_fail(self, checker, mock_redis_client):
        mock_redis_client.get.return_value = "hello world"
        with pytest.raises(AssertionError, match="does not contain"):
            checker.assert_value_contains("k", "missing_text")

    def test_assert_key_not_exists_on_missing(self, checker, mock_redis_client):
        mock_redis_client.exists.return_value = False
        mock_redis_client.get.return_value = None
        with pytest.raises(AssertionError, match="not found"):
            checker.assert_value_equals("missing_key", "any")
