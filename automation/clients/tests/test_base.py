import asyncio
import time

import pytest

from automation.clients.base import (
    BaseClient,
    RetryConfig,
    async_retry,
    sync_retry,
)
from automation.clients.exceptions import (
    ConnectionError,
    RetryExhaustedError,
)


class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 30.0
        assert cfg.backoff_factor == 2.0

    def test_custom_values(self):
        cfg = RetryConfig(max_attempts=5, base_delay=0.1, max_delay=5.0)
        assert cfg.max_attempts == 5
        assert cfg.base_delay == 0.1
        assert cfg.max_delay == 5.0


class TestSyncRetry:
    def test_success_on_first_attempt(self):
        call_count = 0

        @sync_retry()
        def operation():
            nonlocal call_count
            call_count += 1
            return "success"

        result = operation()
        assert result == "success"
        assert call_count == 1

    def test_retry_on_failure_then_success(self):
        call_count = 0

        @sync_retry(RetryConfig(max_attempts=3, base_delay=0.01))
        def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary failure", host="test")
            return "recovered"

        result = operation()
        assert result == "recovered"
        assert call_count == 3

    def test_exhaust_retries(self):
        call_count = 0

        @sync_retry(RetryConfig(max_attempts=3, base_delay=0.01))
        def operation():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("persistent failure", host="test")

        with pytest.raises(RetryExhaustedError):
            operation()
        assert call_count == 3

    def test_non_retryable_exception(self):
        @sync_retry(retryable_exceptions=(ConnectionError,))
        def operation():
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            operation()


@pytest.mark.asyncio
class TestAsyncRetry:
    async def test_async_success(self):
        call_count = 0

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.01))
        async def operation():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await operation()
        assert result == "ok"
        assert call_count == 1

    async def test_async_retry_then_success(self):
        call_count = 0

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.01))
        async def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temp fail", host="test")
            return "ok"

        result = await operation()
        assert result == "ok"
        assert call_count == 3

    async def test_async_exhaust(self):
        call_count = 0

        @async_retry(RetryConfig(max_attempts=2, base_delay=0.01))
        async def operation():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("fail", host="test")

        with pytest.raises(RetryExhaustedError):
            await operation()
        assert call_count == 2


class TestBaseClient:
    def test_initial_state(self):
        client = BaseClient("TestClient")
        assert client._name == "TestClient"
        assert client.is_connected is False

    def test_connect_not_implemented(self):
        client = BaseClient()
        with pytest.raises(NotImplementedError):
            client.connect()

    def test_close_not_implemented(self):
        client = BaseClient()
        with pytest.raises(NotImplementedError):
            client.close()
