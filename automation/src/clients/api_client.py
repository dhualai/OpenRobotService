import json
import os
import httpx
import allure
from typing import Any, Dict, Optional

from automation.config import load_config
from automation.config.models import ApiConfig
from automation.src.clients.base import BaseClient
from automation.src.utils.retry import async_retry, RetryConfig
from automation.src.clients.exceptions import AuthenticationError, ClientConnectionError, ClientTimeoutError

# Apply redaction only to report data. Callers still receive the original response.
_REDACTED = "***"
_SENSITIVE_HEADERS = {
    "authorization", "cookie", "set-cookie", "proxy-authorization",
    "x-api-key", "x-auth-token", "x-access-token", "x-refresh-token",
}
_SENSITIVE_BODY_KEYS = {
    "password", "passwd", "pwd", "token", "accesstoken", "refreshtoken",
    "idtoken", "sessiontoken", "secret", "clientsecret", "apikey",
    "credential", "credentials", "authorization", "cookie", "setcookie",
}


def _attachment_url(url: str) -> str:
    """Keep the request path while omitting URL components that can carry credentials."""
    try:
        return str(httpx.URL(url).copy_with(username=None, password=None, query=None, fragment=None))
    except httpx.InvalidURL:
        return _REDACTED


def _mask_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    masked = {}
    for key, value in headers.items():
        name = str(key).lower()
        if name in _SENSITIVE_HEADERS:
            value = _REDACTED
        elif name in {"location", "content-location"}:
            value = _attachment_url(str(value))
        masked[key] = value
    return masked


def _mask_body(obj: Any) -> Any:
    if isinstance(obj, dict):
        masked = {}
        for key, value in obj.items():
            name = str(key).lower().replace("_", "").replace("-", "")
            masked[key] = _REDACTED if name in _SENSITIVE_BODY_KEYS else _mask_body(value)
        return masked
    if isinstance(obj, list):
        return [_mask_body(x) for x in obj]
    return obj


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
        if config is not None:
            self._cfg = config
        else:
            base_url = os.getenv("OPENROBOT_API_BASE_URL")
            if base_url:
                self._cfg = ApiConfig(base_url=base_url)
            else:
                self._cfg = load_config().api
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
        """Attach the request with sensitive fields masked."""
        full_url = f"{self._cfg.base_url.rstrip('/')}/{url}"
        detail: Dict[str, Any] = {
            "method": method.upper(),
            "url": _attachment_url(full_url),
        }
        headers = kwargs.get("headers", {})
        if headers:
            try:
                detail["headers"] = _mask_headers(dict(headers))
            except Exception:
                pass
        params = kwargs.get("params", {})
        if params:
            try:
                detail["params"] = _mask_body(dict(params))
            except Exception:
                pass
        json_body = kwargs.get("json")
        if json_body is not None:
            detail["body"] = _mask_body(json_body)
        else:
            data_body = kwargs.get("data")
            if data_body is not None:
                try:
                    detail["body"] = _mask_body(dict(data_body))
                except Exception:
                    pass
        try:
            allure.attach(
                json.dumps(detail, indent=2, ensure_ascii=False, default=str),
                name="Request",
                attachment_type=allure.attachment_type.JSON,
            )
        except Exception:
            pass

    def _allure_attach_response(self, response: httpx.Response) -> None:
        """Attach response metadata and its redacted structured body."""
        try:
            status_code = response.status_code
        except Exception:
            status_code = 0

        detail: Dict[str, Any] = {
            "status_code": status_code,
            "url": _attachment_url(str(getattr(response, "url", ""))),
        }
        try:
            detail["reason"] = str(response.reason_phrase)
        except Exception:
            pass
        try:
            resp_headers = dict(response.headers)
            if resp_headers:
                detail["headers"] = _mask_headers(resp_headers)
        except Exception:
            pass
        try:
            has_body = bool(response.content)
        except Exception:
            has_body = False
        if has_body:
            try:
                body = response.json()
            except (ValueError, TypeError):
                body = None
            if isinstance(body, (dict, list)):
                detail["body"] = _mask_body(body)
            else:
                detail["body"] = "[unstructured response body omitted]"
        try:
            allure.attach(
                json.dumps(detail, indent=2, ensure_ascii=False, default=str),
                name="Response",
                attachment_type=allure.attachment_type.JSON,
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
