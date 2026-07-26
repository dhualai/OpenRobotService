from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from automation.config.models import ApiConfig
from automation.clients.api_client import ApiClient
from automation.clients.exceptions import (
    AuthenticationError,
    ConnectionError,
    TimeoutError,
)


@pytest.mark.asyncio
class TestApiClient:
    async def test_connect(self):
        client = ApiClient(config=ApiConfig(base_url="http://test.local"))
        await client.connect()
        assert client.is_connected is True
        assert client._client is not None
        await client.close()

    async def test_connect_sets_base_url(self):
        client = ApiClient(config=ApiConfig(base_url="http://test.local"))
        await client.connect()
        assert client.base_url == "http://test.local"
        await client.close()

    async def test_close(self):
        client = ApiClient()
        await client.connect()
        await client.close()
        assert client.is_connected is False
        assert client._client is None

    async def test_async_context_manager(self):
        async with ApiClient(ApiConfig(base_url="http://test.local")) as client:
            assert client.is_connected is True
        assert client.is_connected is False

    async def test_request_without_connect_raises(self):
        client = ApiClient()
        with pytest.raises(ConnectionError, match="Client not connected"):
            await client.request("GET", "/test")

    async def test_successful_request(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = mock_response

        client = ApiClient(ApiConfig(base_url="http://test.local"))
        client._client = mock_client
        client._connected = True

        result = await client.request("GET", "/api/test")
        assert result.status_code == 200

    async def test_request_authentication_error(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.text = "unauthorized"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = mock_response

        client = ApiClient(ApiConfig(base_url="http://test.local"))
        client._client = mock_client
        client._connected = True

        with pytest.raises(AuthenticationError):
            await client.request("GET", "/api/secret")

    async def test_request_connection_error(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = httpx.ConnectError("connection refused")

        client = ApiClient(ApiConfig(base_url="http://test.local"))
        client._client = mock_client
        client._connected = True

        with pytest.raises(ConnectionError):
            await client.request("GET", "/api/test")

    async def test_request_timeout(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = httpx.TimeoutException("timeout")

        client = ApiClient(ApiConfig(base_url="http://test.local"))
        client._client = mock_client
        client._connected = True

        with pytest.raises(TimeoutError):
            await client.request("GET", "/api/test")

    async def test_config_loaded_from_defaults(self):
        client = ApiClient()
        assert client._cfg.base_url == "http://localhost:8000"
