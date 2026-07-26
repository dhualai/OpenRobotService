import httpx
from typing import Any, Dict, Optional

from automation.config import load_config
from automation.config.models import ApiConfig
from automation.framework.clients.base import BaseClient, async_retry, RetryConfig
from automation.framework.clients.exceptions import AuthenticationError, ConnectionError, TimeoutError


class ApiClient(BaseClient):
    """HTTP API client with retry, timeout, logging, and exception handling.

    Wraps httpx.AsyncClient for making API requests to the backend.
    """

    def __init__(self, config: Optional[ApiConfig] = None, retry_config: Optional[RetryConfig] = None):
        super().__init__(name="ApiClient")
        self._cfg = config or load_config().api
        self._retry_cfg = retry_config or RetryConfig()
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "ApiClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._cfg.base_url,
            timeout=httpx.Timeout(self._cfg.timeout),
        )
        self._connected = True
        self._log.info("Connected to API: %s", self._cfg.base_url)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            self._connected = False
            self._log.info("API client disconnected")

    @property
    def base_url(self) -> str:
        return self._cfg.base_url

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make an HTTP request with retry and logging.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: URL path relative to base_url
            **kwargs: Additional arguments passed to httpx.AsyncClient.request

        Returns:
            httpx.Response object

        Raises:
            ConnectionError: If the request fails due to connection issues
            TimeoutError: If the request times out
            AuthenticationError: If authentication fails (401/403)
        """
        if not self._client:
            raise ConnectionError("Client not connected. Call connect() first.", host=self._cfg.base_url)

        url = path.lstrip("/")
        self._log.debug("Request: %s %s", method.upper(), url)

        try:
            response: httpx.Response = await self._send_with_retry(method, url, **kwargs)
            self._log.debug("Response: %s %s -> %s", method.upper(), url, response.status_code)

            if response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {response.status_code} {response.text}")

            response.raise_for_status()
            return response

        except httpx.ConnectError as e:
            raise ConnectionError(f"Connection error: {e}", host=self._cfg.base_url) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Request timed out: {e}", timeout=self._cfg.timeout) from e

    @async_retry()
    async def _send_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not self._client:
            raise ConnectionError("Client not connected", host=self._cfg.base_url)
        return await self._client.request(method, url, **kwargs)
