"""Playwright network capture and Allure attachment helpers."""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import allure
from playwright.sync_api import Page, Request, Response


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "password",
    "refresh_token",
    "set-cookie",
    "token",
}
BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


@dataclass
class CapturedExchange:
    """One request/response pair associated with a business step."""

    sequence: int
    method: str
    url: str
    status: int | None = None
    request_headers: dict[str, Any] = field(default_factory=dict)
    request_body: Any = None
    response_headers: dict[str, Any] = field(default_factory=dict)
    response_body: Any = None
    duration_ms: float | None = None


@dataclass
class CapturedStep:
    """All traffic and evidence captured for one business step."""

    step_id: str
    role: str
    action: str
    exchanges: list[CapturedExchange] = field(default_factory=list)
    error: str | None = None


def build_step_evidence(captured: CapturedStep) -> dict[str, Any]:
    """Build a readable step result and response summary for Allure."""

    interfaces = []
    for item in captured.exchanges:
        status = item.status
        if status is None:
            result = "未响应"
        elif status < 400:
            result = "通过"
        else:
            result = "失败"
        interfaces.append(
            {
                "method": item.method,
                "path": urlsplit(item.url).path,
                "status": status,
                "result": result,
            }
        )

    step_passed = captured.error is None
    return {
        "step_id": captured.step_id,
        "role": captured.role,
        "action": captured.action,
        "assertion": {
            "name": "业务步骤执行",
            "expected": "步骤无异常完成",
            "actual": "无异常" if step_passed else captured.error,
            "result": "通过" if step_passed else "失败",
        },
        "interfaces": interfaces,
    }


def redact_value(value: Any) -> Any:
    """Recursively redact credentials and bearer tokens."""

    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if key.lower() in SENSITIVE_KEYS
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return BEARER_RE.sub(rf"\1{REDACTED}", value)
    return value


def redact_url(url: str) -> str:
    """Redact sensitive query parameters from a captured URL."""

    parts = urlsplit(url)
    query = urlencode(
        [
            (key, REDACTED if key.lower() in SENSITIVE_KEYS else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ],
        quote_via=quote,
        safe="[]",
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


class NetworkCapture:
    """Capture actual browser traffic and attach it to Allure steps."""

    def __init__(self, page: Page, max_body_chars: int = 20_000):
        self.page = page
        self.max_body_chars = max_body_chars
        self._active_step: CapturedStep | None = None
        self._exchange_by_request: dict[int, CapturedExchange] = {}
        self._request_started: dict[int, float] = {}
        self._sequence = 0
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    @contextmanager
    def step(self, step_id: str, role: str, action: str) -> Iterator[CapturedStep]:
        """Capture and attach one business step."""

        if self._active_step is not None:
            raise RuntimeError("nested capture steps are not supported")

        captured = CapturedStep(step_id=step_id, role=role, action=action)
        self._active_step = captured
        with allure.step(f"{step_id} [{role}] {action}"):
            try:
                yield captured
            except Exception as exc:
                captured.error = str(exc)
                raise
            finally:
                self._active_step = None
                self._attach(captured)

    def _on_request(self, request: Request) -> None:
        if self._active_step is None:
            return
        self._sequence += 1
        exchange = CapturedExchange(
            sequence=self._sequence,
            method=request.method,
            url=redact_url(request.url),
            request_headers=redact_value(dict(request.headers)),
            request_body=self._safe_payload(request.post_data),
        )
        self._active_step.exchanges.append(exchange)
        self._exchange_by_request[id(request)] = exchange
        self._request_started[id(request)] = time.perf_counter()

    def _on_response(self, response: Response) -> None:
        exchange = self._exchange_by_request.get(id(response.request))
        if exchange is None:
            return
        started = self._request_started.pop(id(response.request), None)
        exchange.status = response.status
        exchange.response_headers = redact_value(dict(response.headers))
        exchange.duration_ms = (
            round((time.perf_counter() - started) * 1000, 2)
            if started is not None
            else None
        )
        content_type = response.headers.get("content-type", "")
        if any(kind in content_type for kind in ("json", "text", "event-stream")):
            try:
                exchange.response_body = self._safe_payload(response.text())
            except Exception:
                exchange.response_body = None

    def _safe_payload(self, payload: str | None) -> Any:
        if not payload:
            return None
        text = payload[: self.max_body_chars]
        try:
            return redact_value(json.loads(text))
        except Exception:
            return BEARER_RE.sub(rf"\1{REDACTED}", text)

    def _attach(self, captured: CapturedStep) -> None:
        for item in captured.exchanges:
            endpoint = urlsplit(item.url).path
            status = item.status if item.status is not None else "未响应"
            with allure.step(f"接口：{item.method} {endpoint} -> HTTP {status}"):
                pass
        allure.attach(
            json.dumps(
                build_step_evidence(captured),
                ensure_ascii=False,
                indent=2,
            ),
            name=f"{captured.step_id}-断言与响应摘要",
            attachment_type=allure.attachment_type.JSON,
        )
        allure.attach(
            json.dumps(
                {
                    "step_id": captured.step_id,
                    "role": captured.role,
                    "action": captured.action,
                    "error": captured.error,
                    "exchanges": [
                        {
                            "sequence": item.sequence,
                            "method": item.method,
                            "url": item.url,
                            "status": item.status,
                            "duration_ms": item.duration_ms,
                            "request_headers": item.request_headers,
                            "request_body": item.request_body,
                            "response_headers": item.response_headers,
                            "response_body": item.response_body,
                        }
                        for item in captured.exchanges
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            name=f"{captured.step_id}-网络请求",
            attachment_type=allure.attachment_type.JSON,
        )
        try:
            allure.attach(
                self.page.screenshot(full_page=True),
                name=f"{captured.step_id}-页面截图",
                attachment_type=allure.attachment_type.PNG,
            )
        except Exception:
            pass
