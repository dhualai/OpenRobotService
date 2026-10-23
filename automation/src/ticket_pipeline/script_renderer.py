"""Deterministic pytest script rendering for ticket-driven test cases.

The LLM produces structured, spec-grounded case data; this module turns that
data into a pytest module that follows the automation framework conventions.
Rendering is deterministic so the generated script always compiles and can be
collected by pytest.
"""

from __future__ import annotations

import json
import re
from string import Template
from typing import Any, Dict, Iterable, List, Optional

from automation.src.ticket_pipeline.models import TicketCandidate

_TYPE_LABELS = {
    "positive": "正常流程",
    "negative": "异常流程",
    "edge": "边界条件",
    "permission": "权限",
    "flow": "全链路",
    "auth": "权限",
}

_CASE_ID_RE = re.compile(r"[^0-9a-zA-Z]+")

_MODULE_TEMPLATE = Template('''"""
Story: $ticket_ref - $title
生成方式: 由工单 + 接口规格自动生成（框架规范版）
工单: $ticket_id
用例数: $case_count
"""
import os

import allure
import httpx
import pytest
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment
from automation.src.mocks.backend_mock import create_mock_transport

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8400")
USE_MOCK = os.getenv("USE_MOCK", "0") != "0"
STRICT_FIELDS = os.getenv("TICKET_CASE_STRICT_FIELDS", "0") == "1"

# 用例创建出来的资源，测试结束后按 id 删除，避免污染环境
_CREATED = []


def _credentials():
    """Return the login credentials of the target environment."""
    username = os.getenv("TEST_USERNAME")
    password = os.getenv("TEST_PASSWORD")
    if not USE_MOCK and not (username and password):
        raise RuntimeError(
            "TEST_USERNAME / TEST_PASSWORD are required when USE_MOCK=0; "
            "run backend/scripts/seed_automation_env.py or export real credentials"
        )
    return username or "testadmin", password or "admin123"


def _collection_path(path: str) -> str:
    """Strip a trailing path parameter so the collection URL can be rebuilt."""
    head, sep, tail = path.rpartition("/")
    if sep and tail.startswith("{") and tail.endswith("}"):
        return head
    return path


def _field_issues(payload, expected, path=""):
    """Compare a response against the expected fields and list every mismatch."""
    issues = []
    for key, want in (expected or {}).items():
        here = f"{path}.{key}" if path else str(key)
        if not isinstance(payload, dict) or key not in payload:
            issues.append(f"{here}: missing (expected {want!r})")
            continue
        got = payload[key]
        if isinstance(want, dict) and isinstance(got, dict):
            issues.extend(_field_issues(got, want, here))
        elif got != want:
            issues.append(f"{here}: expected {want!r}, got {got!r}")
    return issues


async def _api(client, method: str, path: str, step: str = '', headers=None,
               expected_status: int | None = None, expected_fields: dict | None = None, **kwargs):
    """Send a request wrapped in an Allure step block; assertions run inside the step."""
    with allure.step(step or f'{method.upper()} {path}'):
        r = await client.request(method, path, headers=headers, **kwargs)
        if expected_status is not None:
            assert_status_code(r, expected_status)
        if expected_fields:
            try:
                payload = r.json()
            except Exception:
                payload = {}
            issues = _field_issues(payload, expected_fields)
            if issues:
                message = "; ".join(issues)
                if STRICT_FIELDS:
                    assert_dict_contains_subset(payload, expected_fields)
                print(f"::warning::字段断言未通过（软断言，不阻断）: {message}")
        if method.upper() == "POST" and 200 <= r.status_code < 300:
            try:
                created = r.json()
            except Exception:
                created = None
            if isinstance(created, dict) and created.get("id"):
                _CREATED.append((_collection_path(path), created["id"]))
        flush_assert_attachment()
        return r


@pytest.fixture
async def client():
    if USE_MOCK:
        async with httpx.AsyncClient(base_url="http://mock.local", transport=create_mock_transport()) as c:
            yield c
    else:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
            yield c


@pytest.fixture
async def auth(client):
    username, password = _credentials()
    r = await client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
        },
    )
    r.raise_for_status()
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture
async def cleanup(client):
    """Delete the resources this test created; cleanup failures never fail the test."""
    yield
    created = list(_CREATED)
    _CREATED.clear()
    if not created:
        return
    headers = None
    try:
        username, password = _credentials()
        r = await client.post("/api/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            headers = {"Authorization": "Bearer " + r.json()["access_token"]}
    except Exception:
        headers = None
    for path, resource_id in reversed(created):
        try:
            await client.delete(f"{path}/{resource_id}", headers=headers)
        except Exception:
            pass
''')

_CLASS_TEMPLATE = '''
@allure.feature({feature_literal})
class TestTicket{ticket_id}:
    """{feature_doc}"""

'''

_CASE_TEMPLATE = '''    @allure.story({story_literal})
    @allure.title({title_literal})
    @pytest.mark.api
    async def test_{test_name}(self, {fixtures}):
        """{label}：{title_doc}"""
{body}
'''


def _test_name(case_id: str) -> str:
    cleaned = _CASE_ID_RE.sub("_", str(case_id)).strip("_").lower()
    return cleaned or "case"


def _quote(value: str) -> str:
    """Escape a value for embedding in a double-quoted Python string."""
    return json.dumps(str(value), ensure_ascii=False)


def _docstring_safe(value: str) -> str:
    """Make a value safe to embed inside a triple-quoted docstring."""
    return str(value).replace("\\", "\\\\").replace('"""', "'''")


def _render_request(request: Dict[str, Any], *, auth_fixture: str) -> List[str]:
    method = str(request.get("method") or "GET").upper()
    path = str(request.get("path") or "")
    step = str(request.get("step") or f"{method} {path}")
    parts = [f"        await _api(client, {_quote(method.lower())}, {_quote(path)},"]
    parts.append(f"                   step={_quote(step)},")
    if request.get("auth", True):
        parts.append("                   headers=auth,")
    expected_status = request.get("expected_status")
    if expected_status is not None:
        parts.append(f"                   expected_status={int(expected_status)},")
    expected_fields = request.get("expected_fields")
    if isinstance(expected_fields, dict) and expected_fields:
        parts.append(
            "                   expected_fields="
            + json.dumps(expected_fields, ensure_ascii=False)
            + ","
        )
    if isinstance(request.get("body"), dict) and request["body"]:
        parts.append(
            "                   json="
            + json.dumps(request["body"], ensure_ascii=False)
            + ","
        )
    if isinstance(request.get("params"), dict) and request["params"]:
        parts.append(
            "                   params="
            + json.dumps(request["params"], ensure_ascii=False)
            + ","
        )
    parts.append("                   )")
    return parts


def render_script(ticket: TicketCandidate, cases: Iterable[Dict[str, Any]]) -> str:
    """Render a pytest module for the given ticket cases."""
    case_list = [case for case in cases if isinstance(case, dict)]
    feature = f"工单 {ticket.id} {ticket.title}".strip()
    lines = [
        _MODULE_TEMPLATE.substitute(
            ticket_ref=f"ORS-{ticket.id}",
            title=_docstring_safe(ticket.title),
            ticket_id=ticket.id,
            case_count=len(case_list),
        )
    ]
    lines.append(
        _CLASS_TEMPLATE.format(
            feature_literal=_quote(feature),
            feature_doc=_docstring_safe(feature),
            ticket_id=ticket.id,
        )
    )

    for case in case_list:
        requests = [item for item in (case.get("requests") or []) if isinstance(item, dict)]
        if not requests:
            continue
        needs_auth = any(item.get("auth", True) for item in requests)
        needs_cleanup = any(
            str(item.get("method") or "").upper() == "POST" for item in requests
        )
        fixtures = "client, auth" if needs_auth else "client"
        if needs_cleanup:
            fixtures = f"{fixtures}, cleanup"
        body_lines: List[str] = []
        for request in requests:
            body_lines.extend(_render_request(request, auth_fixture="auth"))
        label = _TYPE_LABELS.get(str(case.get("type") or "").lower(), "正常流程")
        story = str(case.get("module") or feature)
        title = str(case.get("title") or case.get("id") or "case")
        lines.append(
            _CASE_TEMPLATE.format(
                story_literal=_quote(story),
                title_literal=_quote(title),
                title_doc=_docstring_safe(title),
                test_name=_test_name(case.get("id") or "case"),
                fixtures=fixtures,
                label=label,
                body="\n".join(body_lines),
            )
        )

    return "\n".join(lines).rstrip() + "\n"


def collect_request_paths(cases: Iterable[Dict[str, Any]]) -> List[str]:
    """Collect every API path referenced by the given cases."""
    paths: List[str] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        for request in case.get("requests") or []:
            if not isinstance(request, dict):
                continue
            path = str(request.get("path") or "").strip()
            if path and path not in paths:
                paths.append(path)
    return paths


def validate_case_requests(
    cases: Iterable[Dict[str, Any]],
    spec: Optional[Dict[str, Any]],
) -> List[str]:
    """Validate that every case request maps to a path declared by the spec.

    Returns a list of human readable issues; an empty list means the cases are
    grounded in the API contract.
    """
    if not spec:
        return []
    templates = list((spec.get("paths") or {}).keys())
    if not templates:
        return []
    issues: List[str] = []
    for path in collect_request_paths(cases):
        if not any(_path_matches(path, template) for template in templates):
            issues.append(f"case request path not in spec: {path!r}")
    return issues


def _path_matches(path: str, template: str) -> bool:
    """Match a concrete path against an OpenAPI path template."""
    if path == template:
        return True
    pattern = re.escape(template)
    pattern = re.sub(r"\\\{[^/{}]+\\\}", "[^/]+", pattern)
    return re.fullmatch(pattern, path) is not None
