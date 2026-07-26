import asyncio
import time
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type

from pydantic import BaseModel, Field


class RetryExhaustedError(Exception):
    def __init__(self, message: str, attempt_count: int):
        self.attempt_count = attempt_count
        super().__init__(message)


class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1)
    base_delay: float = Field(default=1.0, ge=0)
    max_delay: float = Field(default=30.0, ge=0)
    backoff_factor: float = Field(default=2.0, ge=1.0)


def _resolve_exceptions() -> Tuple[Type[Exception], ...]:
    from automation.infrastructure.clients.exceptions import ClientConnectionError, ClientTimeoutError
    return (ClientConnectionError, ClientTimeoutError)


def retry(
    retry_config: Optional[RetryConfig] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    sync: bool = True,
) -> Callable:
    cfg = retry_config or RetryConfig()
    exc_types = retryable_exceptions or _resolve_exceptions()
    sleep_fn = time.sleep if sync else asyncio.sleep

    def _make_wrapper(func):
        if sync:
            @wraps(func)
            def wrapper(*args, **kwargs):
                last_exc = None
                for attempt in range(1, cfg.max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except exc_types as e:
                        last_exc = e
                        if attempt < cfg.max_attempts:
                            delay = min(cfg.base_delay * (cfg.backoff_factor ** (attempt - 1)), cfg.max_delay)


                            sleep_fn(delay)
                raise RetryExhaustedError(
                    "All %d retry attempts failed" % cfg.max_attempts,
                    attempt_count=cfg.max_attempts,
                ) from last_exc
            return wrapper
        else:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                last_exc = None
                for attempt in range(1, cfg.max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exc_types as e:
                        last_exc = e
                        if attempt < cfg.max_attempts:
                            delay = min(cfg.base_delay * (cfg.backoff_factor ** (attempt - 1)), cfg.max_delay)


                            await asyncio.sleep(delay)
                raise RetryExhaustedError(
                    "All %d retry attempts failed" % cfg.max_attempts,
                    attempt_count=cfg.max_attempts,
                ) from last_exc
            return wrapper

    return _make_wrapper


def sync_retry(
    retry_config: Optional[RetryConfig] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
) -> Callable:
    return retry(retry_config=retry_config, retryable_exceptions=retryable_exceptions, sync=True)


def async_retry(
    retry_config: Optional[RetryConfig] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
) -> Callable:
    return retry(retry_config=retry_config, retryable_exceptions=retryable_exceptions, sync=False)
