import asyncio
import time
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type

from pydantic import BaseModel, Field

from automation.clients.exceptions import (
    ClientError,
    ConnectionError,
    QueryError,
    RetryExhaustedError,
    TimeoutError,
)
from automation.logger import get_logger


class RetryConfig(BaseModel):
    """Configuration for retry behavior."""

    max_attempts: int = Field(default=3, ge=1, description="Maximum retry attempts")
    base_delay: float = Field(default=1.0, ge=0, description="Base delay in seconds")
    max_delay: float = Field(default=30.0, ge=0, description="Maximum delay in seconds")
    backoff_factor: float = Field(default=2.0, ge=1.0, description="Exponential backoff multiplier")


def sync_retry(
    retry_config: Optional[RetryConfig] = None,
    retryable_exceptions: Tuple[Type[Exception], ...] = (ConnectionError, TimeoutError),
) -> Callable:
    """Decorator: retry a sync function with exponential backoff.

    Args:
        retry_config: Retry configuration. Uses defaults if None.
        retryable_exceptions: Tuple of exception types that trigger retry.
    """
    cfg = retry_config or RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(1, cfg.max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < cfg.max_attempts:
                        delay = min(cfg.base_delay * (cfg.backoff_factor ** (attempt - 1)), cfg.max_delay)
                        time.sleep(delay)
            raise RetryExhaustedError(
                f"All {cfg.max_attempts} retry attempts failed for {func.__name__}",
                attempt_count=cfg.max_attempts,
            ) from last_exc
        return wrapper
    return decorator


def async_retry(
    retry_config: Optional[RetryConfig] = None,
    retryable_exceptions: Tuple[Type[Exception], ...] = (ConnectionError, TimeoutError),
) -> Callable:
    """Decorator: retry an async function with exponential backoff."""
    cfg = retry_config or RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(1, cfg.max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < cfg.max_attempts:
                        delay = min(cfg.base_delay * (cfg.backoff_factor ** (attempt - 1)), cfg.max_delay)
                        await asyncio.sleep(delay)
            raise RetryExhaustedError(
                f"All {cfg.max_attempts} retry attempts failed for {func.__name__}",
                attempt_count=cfg.max_attempts,
            ) from last_exc
        return wrapper
    return decorator


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

    def _wrap_connection_error(self, exc: Exception, host: str = "", port: int = 0) -> ConnectionError:
        return ConnectionError(f"Connection failed: {exc}", host=host, port=port)

    def _wrap_query_error(self, exc: Exception, query: str = "") -> QueryError:
        return QueryError(f"Query failed: {exc}", query=query)
