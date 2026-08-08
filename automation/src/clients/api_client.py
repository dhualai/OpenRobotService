import json
import httpx
import allure
from typing import Any, Dict, Optional

from automation.config import load_config
from automation.config.models import ApiConfig
from automation.src.clients.base import BaseClient
from automation.src.utils.retry import async_retry, RetryConfig
from automation.src.clients.exceptions import AuthenticationError, ClientConnectionError, ClientTimeoutError


class ApiClient(BaseClient):
    """HTTP API client with retry, timeout, logging, and exception handling.

    Wraps httpx.AsyncClient for making API requests to the backend.
    All request/response details are automatically attached to Allure reports.
    """

    def __init__(
        self,
        config: Optional[ApiConfig] = None,
        retry_config: Optional[RetryConfig] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        raise_auth_errors: bool = False,
    ):
        super().__init__(name="ApiClient")
        self._cfg = config or load_config().api
        self._retry_cfg = retry_config or RetryConfig()
        self._transport = transport
        self._raise_auth_errors = raise_auth_errors
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "ApiClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._cfg.base_url,
            transport=self._transport,
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
        """Make an HTTP request with retry, logging, and Allure attachments.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: URL path relative to base_url
            **kwargs: Additional arguments passed to httpx.AsyncClient.request

        Returns:
            httpx.Response object

        Raises:
            ClientConnectionError: If the request fails due to connection issues
            ClientTimeoutError: If the request times out
            AuthenticationError: If authentication fails (401/403) and raise_auth_errors is enabled
        """
        if not self._client:
            raise ClientConnectionError("Client not connected. Call connect() first.", host=self._cfg.base_url)

        url = path.lstrip("/")
        self._log.debug("Request: %s %s", method.upper(), url)

        try:
            response: httpx.Response = await self._send_with_retry(method, url, **kwargs)
            self._log.debug("Response: %s %s -> %s", method.upper(), url, response.status_code)

            self._allure_attach_response(response)

            if response.status_code in (401, 403) and self._raise_auth_errors:
                raise AuthenticationError(f"Authentication failed: {response.status_code} {response.text}")

            return response

        except httpx.ConnectError as e:
            self._allure_attach_error("ConnectionError", str(e))
            raise ClientConnectionError(f"Connection error: {e}", host=self._cfg.base_url) from e
        except httpx.TimeoutException as e:
            self._allure_attach_error("TimeoutError", str(e))
            raise ClientTimeoutError(f"Request timed out: {e}", timeout=self._cfg.timeout) from e

    @async_retry()
    async def _send_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not self._client:
            raise ClientConnectionError("Client not connected", host=self._cfg.base_url)
        self._allure_attach_request(method, url, **kwargs)
        return await self._client.request(method, url, **kwargs)
    # — HTTP convenience methods —

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)

    # — Allure attachments —

    # ── Allure attachments ──────────────────────────────────────────────

    def _allure_attach_request(self, method: str, url: str, **kwargs: Any) -> None:
        """Attach request details to Allure report."""
        full_url = f"{self._cfg.base_url.rstrip('/')}/{url}"

        allure.attach(
            json.dumps({"method": method.upper(), "url": full_url}, indent=2, ensure_ascii=False),
            name="Request URL",
            attachment_type=allure.attachment_type.JSON,
        )

        headers = kwargs.get("headers", {})
        if headers:
            try:
                allure.attach(
                    json.dumps(dict(headers), indent=2, ensure_ascii=False, default=str),
                    name="Request Headers",
                    attachment_type=allure.attachment_type.JSON,
                )
            except Exception:
                pass

        params = kwargs.get("params", {})
        if params:
            try:
                allure.attach(
                    json.dumps(dict(params), indent=2, ensure_ascii=False, default=str),
                    name="Request Params",
                    attachment_type=allure.attachment_type.JSON,
                )
            except Exception:
                pass

        json_body = kwargs.get("json")
        if json_body is not None:
            try:
                allure.attach(
                    json.dumps(json_body, indent=2, ensure_ascii=False, default=str),
                    name="Request Body (JSON)",
                    attachment_type=allure.attachment_type.JSON,
                )
            except Exception:
                pass
        else:
            data_body = kwargs.get("data")
            if data_body is not None:
                try:
                    allure.attach(
                        json.dumps(dict(data_body), indent=2, ensure_ascii=False, default=str),
                        name="Request Body (Form)",
                        attachment_type=allure.attachment_type.JSON,
                    )
                except Exception:
                    pass

    def _allure_attach_response(self, response: httpx.Response) -> None:
        """Attach response details to Allure report."""
        try:
            status_code = response.status_code
        except Exception:
            status_code = 0

        response_info: Dict[str, Any] = {
            "status_code": status_code,
            "url": str(getattr(response, "url", "")),
        }
        try:
            response_info["reason"] = str(response.reason_phrase)
        except Exception:
            pass

        try:
            resp_headers = dict(response.headers)
            if resp_headers:
                response_info["headers"] = resp_headers
        except Exception:
            pass

        try:
            allure.attach(
                json.dumps(response_info, indent=2, ensure_ascii=False, default=str),
                name="Response",
                attachment_type=allure.attachment_type.JSON,
            )
        except Exception:
            pass

        try:
            body_text = response.text
        except Exception:
            body_text = ""

        if body_text:
            try:
                body = response.json()
                allure.attach(
                    json.dumps(body, indent=2, ensure_ascii=False, default=str),
                    name="Response Body (JSON)",
                    attachment_type=allure.attachment_type.JSON,
                )
            except (json.JSONDecodeError, ValueError, TypeError):
                allure.attach(
                    body_text,
                    name="Response Body (Text)",
                    attachment_type=allure.attachment_type.TEXT,
                )
            except Exception:
                pass

    def _allure_attach_error(self, error_type: str, message: str) -> None:
        """Attach error details to Allure report."""
        try:
            allure.attach(
                json.dumps({"error_type": error_type, "message": message}, indent=2, ensure_ascii=False),
                name="Error",
                attachment_type=allure.attachment_type.JSON,
            )
        except Exception:
            pass
