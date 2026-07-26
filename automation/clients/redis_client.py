from typing import Any, Optional

from automation.config import load_config
from automation.config.models import RedisConfig
from automation.clients.base import BaseClient, sync_retry, RetryConfig
from automation.clients.exceptions import ConnectionError, QueryError


class RedisClient(BaseClient):
    """Redis cache client with retry, logging, and exception handling.

    Wraps redis.Redis for key-value operations against the cache server.
    """

    def __init__(self, config: Optional[RedisConfig] = None, retry_config: Optional[RetryConfig] = None):
        super().__init__(name="RedisClient")
        self._cfg = config or load_config().redis
        self._retry_cfg = retry_config or RetryConfig()
        self._client: Any = None

    def __enter__(self) -> "RedisClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def connect(self) -> None:
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError("redis library is required. Install: pip install redis")

        self._client = redis_lib.Redis(
            host=self._cfg.host,
            port=self._cfg.port,
            password=self._cfg.password or None,
            db=self._cfg.db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
        )
        self._client.ping()
        self._connected = True
        self._log.info("Connected to Redis: %s:%s/%s", self._cfg.host, self._cfg.port, self._cfg.db)

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._connected = False
            self._log.info("Redis client disconnected")

    def get(self, key: str) -> Optional[str]:
        """Get a value by key.

        Raises:
            ConnectionError: If not connected
            QueryError: If the operation fails
        """
        if not self._client:
            raise ConnectionError("Redis not connected", host=self._cfg.host, port=self._cfg.port)
        try:
            return self._get_with_retry(key)
        except Exception as e:
            raise self._wrap_query_error(e, query=f"GET {key}")

    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set a key-value pair with optional expiry in seconds."""
        if not self._client:
            raise ConnectionError("Redis not connected", host=self._cfg.host, port=self._cfg.port)
        try:
            return self._set_with_retry(key, value, ex)
        except Exception as e:
            raise self._wrap_query_error(e, query=f"SET {key}")

    def delete(self, key: str) -> bool:
        """Delete a key."""
        if not self._client:
            raise ConnectionError("Redis not connected", host=self._cfg.host, port=self._cfg.port)
        try:
            result = self._delete_with_retry(key)
            return result > 0
        except Exception as e:
            raise self._wrap_query_error(e, query=f"DEL {key}")

    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        if not self._client:
            raise ConnectionError("Redis not connected", host=self._cfg.host, port=self._cfg.port)
        try:
            result = self._exists_with_retry(key)
            return result > 0
        except Exception as e:
            raise self._wrap_query_error(e, query=f"EXISTS {key}")

    @sync_retry()
    def _get_with_retry(self, key: str) -> Optional[str]:
        return self._client.get(key)

    @sync_retry()
    def _set_with_retry(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        return self._client.set(key, value, ex=ex)

    @sync_retry()
    def _delete_with_retry(self, key: str) -> int:
        return self._client.delete(key)

    @sync_retry()
    def _exists_with_retry(self, key: str) -> int:
        return self._client.exists(key)
