"""Redis data checker: verify cache state matches expectations."""

from typing import Optional

from automation.clients.redis_client import RedisClient


class RedisChecker:
    """Assert Redis cache state using RedisClient.

    Usage:
        checker = RedisChecker(client)
        checker.assert_key_exists("session:abc")
        checker.assert_value_equals("config:mode", "production")
    """

    def __init__(self, client: RedisClient):
        self._client = client

    def assert_key_exists(self, key: str) -> str:
        """Assert key exists, return its value."""
        val = self._client.get(key)
        assert val is not None, f"Key '{key}' not found in Redis"
        return val

    def assert_key_not_exists(self, key: str) -> None:
        """Assert key does not exist."""
        assert not self._client.exists(key), f"Key '{key}' unexpectedly exists in Redis"

    def assert_value_equals(self, key: str, expected: str) -> None:
        """Assert key's value equals expected string."""
        val = self.assert_key_exists(key)
        assert val == expected, f"Redis '{key}': expected {expected!r}, got {val!r}"

    def assert_value_contains(self, key: str, substring: str) -> None:
        """Assert key's value contains substring."""
        val = self.assert_key_exists(key)
        assert substring in val, \
            f"Redis '{key}' does not contain {substring!r}. Value: {val!r}"
