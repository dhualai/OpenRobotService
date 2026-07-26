from automation.utils.retry import RetryConfig, async_retry, sync_retry
from automation.clients.exceptions import (
    ClientError,
    ClientConnectionError,
    ClientTimeoutError,
    QueryError,
    RetryExhaustedError,
)
from automation.logger import get_logger


class BaseClient:
    """Base class for all framework clients.

    Provides unified logging, exception handling, and lifecycle management.
    """

    def __init__(self, name: str = ""):
        self._name = name or self.__class__.__name__
        self._log = get_logger(f"clients.{self._name}")
        self._connected = False

    @property
    def log(self):
        return self._log

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _wrap_connection_error(self, exc: Exception, host: str = "", port: int = 0) -> ClientConnectionError:
        return ClientConnectionError(f"Connection failed: {exc}", host=host, port=port)

    def _wrap_query_error(self, exc: Exception, query: str = "") -> QueryError:
        return QueryError(f"Query failed: {exc}", query=query)
