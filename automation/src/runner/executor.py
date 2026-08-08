"""Test case executor: send request, assert status and fields, attach Allure info.

Supports two shapes:
- single-step (legacy): one request per case (method/path/payload/expected_status)
- multi-step (full flow): `case["steps"]` is a list of steps; placeholders like
  ``{{step1.body.id}}`` / ``{{step1.status}}`` are resolved from previous
  responses before each step is executed.
"""

import json
import re

import allure
import pytest

from automation.src.assertions import assert_status_code

_PH_RE = re.compile(r"\{\{\s*step(\d+)\.(status|body)(?:\s*\.\s*([\w\[\]\"'.\-]+))?\s*\}\}")

_ROLE_CREDENTIALS = {
    "admin": ("testadmin", "admin123"),
    "engineer": ("engineer", "eng123"),
    "customer": ("customer", "cust123"),
}

# module -> Allure feature 中文名
_MODULE_FEATURES = {
    "call": "我要摇人",
    "tasks": "系统任务",
    "admin": "后台管理",
    "auth": "认证",
    "wechat": "微信",
    "ai": "AI",
}

# note 前缀 -> Allure severity（note 格式："覆盖类型：说明"）
_SEVERITY_MAP = {
    "正常流程": allure.severity_level.NORMAL,
    "异常流程": allure.severity_level.CRITICAL,
    "权限": allure.severity_level.BLOCKER,
    "状态流转": allure.severity_level.CRITICAL,
    "数据校验": allure.severity_level.NORMAL,
    "Redis": allure.severity_level.MINOR,
    "AI": allure.severity_level.MINOR,
    "数据库": allure.severity_level.NORMAL,
}


def _attach_allure_meta(case: dict) -> None:
    """Map case data to Allure dynamic labels (feature/story/title/severity) and friendly parameters."""
    module = str(case.get("module") or "").lower()
    note = str(case.get("note") or "").strip()
    path = str(case.get("path") or "")
    steps = case.get("steps")

    allure.dynamic.feature(_MODULE_FEATURES.get(module, module or "通用"))

    # story：优先 note（场景描述），其次 API 路径
    story = note or path
    if module and story:
        story = f"{story}"
    allure.dynamic.story(story or "未命名场景")

    allure.dynamic.title(f"{case.get('id', '')} · {story}" if story else str(case.get("id", "未命名用例")))

    # 覆盖类型前缀提取 severity（支持中英文冒号/逗号分隔）
    prefix = note.split("：")[0].split(":")[0].split(",")[0].split("，")[0]
    allure.dynamic.severity(_SEVERITY_MAP.get(prefix, allure.severity_level.NORMAL))

    method = str(case.get("method", "")).upper()
    expected = case.get("expected_status", "")
    allure.dynamic.description(
        f"**模块**: {_MODULE_FEATURES.get(module, module or '-')}\n\n"
        f"**接口**: `{method} {path}`\n\n"
        f"**预期状态码**: {expected}\n\n"
        f"**场景说明**: {note or '-'}"
    )

    # 友好参数（替换整块 case dict 的展示）
    allure.dynamic.parameter("用例ID", str(case.get("id", "")))
    allure.dynamic.parameter("覆盖类型", prefix or "-")
    if steps:
        allure.dynamic.parameter("链路", f"{len(steps)} 步串联")
    else:
        allure.dynamic.parameter("接口", f"{method} {path}")
        allure.dynamic.parameter("预期状态", str(expected))


async def _auth_for_role(client, role: str) -> dict:
    username, password = _ROLE_CREDENTIALS.get(role, _ROLE_CREDENTIALS["admin"])
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    token = r.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


def _resolve_placeholders(value, ctx: dict, case_id: str):
    """Resolve {{stepN.status}} / {{stepN.body.<path>}} against previous step responses.

    A placeholder that is the entire string keeps its original JSON type
    (e.g. ``{{step1.body.id}}`` yields an int); embedded placeholders become str.
    Raises AssertionError with a clear message when the referenced step/value
    is missing (never silently passes).
    """
    if isinstance(value, dict):
        return {k: _resolve_placeholders(v, ctx, case_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(v, ctx, case_id) for v in value]
    if not isinstance(value, str):
        return value

    def _lookup(match) -> object:
        step_no, kind, rest = int(match.group(1)), match.group(2), match.group(3) or ""
        key = f"step{step_no}"
        if key not in ctx:
            pytest.fail(
                f"[{case_id}] placeholder {match.group(0)!r}: step{step_no} not executed yet "
                f"(context has {sorted(ctx)}); check steps order")
        data = ctx[key]
        if kind == "status":
            return str(data["status"])
        cur = data["body"]
        parts = [p for p in rest.replace("'", "").replace('"', "").split(".") if p]
        for part in parts:
            if isinstance(cur, dict):
                if part not in cur:
                    pytest.fail(
                        f"[{case_id}] placeholder {match.group(0)!r}: step{step_no} response has "
                        f"no field {part!r} (keys: {sorted(cur)})")
                cur = cur[part]
            elif isinstance(cur, list) and part.lstrip("-").isdigit():
                idx = int(part)
                if abs(idx) >= len(cur):
                    pytest.fail(
                        f"[{case_id}] placeholder {match.group(0)!r}: step{step_no} list index "
                        f"{idx} out of range (len={len(cur)})")
                cur = cur[idx]
            else:
                pytest.fail(
                    f"[{case_id}] placeholder {match.group(0)!r}: cannot traverse {part!r} "
                    f"through {type(cur).__name__}")
        return cur

    full = _PH_RE.fullmatch(value)
    if full:
        return _lookup(full)
    return _PH_RE.sub(lambda m: str(_lookup(m)), value)


def _attach_step_allure(case_id: str, step_no: int, total: int, method: str, path: str,
                        payload, expected, r) -> None:
    name = f"{case_id} · step {step_no}/{total}"
    allure.attach(
        json.dumps({
            "step": step_no,
            "url": str(r.request.url),
            "method": method,
            "body": payload,
            "expected_status": expected,
        }, indent=2, ensure_ascii=False),
        name=f"{name} · Request",
        attachment_type=allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps({"status_code": r.status_code, "body": r.text}, indent=2, ensure_ascii=False),
        name=f"{name} · Response",
        attachment_type=allure.attachment_type.JSON,
    )


def _assert_expected_fields(r, expected: dict, case_id: str, step_no=None) -> None:
    data = r.json()
    for key, val in expected.items():
        where = f"step {step_no}" if step_no else "response"
        assert data.get(key) == val, \
            f"[{case_id}] {where}: field {key!r}: expected {val!r}, got {data.get(key)!r}"


async def _run_single(client, auth_header, case) -> None:
    """Legacy single-request path (no `steps`)."""
    role = case.get("role") or "admin"
    if role == "admin":
        headers = auth_header if case.get("auth") == "Y" else {}
    else:
        headers = await _auth_for_role(client, role) if case.get("auth") == "Y" else {}
    payload = case.get("payload") or {}
    method = case["method"].upper()
    path = case["path"]

    with allure.step(f"Request: {method} {path}"):
        r = await client.request(method, path, headers=headers, json=payload)

        req = r.request
        allure.attach(
            json.dumps({
                "url": str(req.url),
                "method": req.method,
                "headers": dict(req.headers),
                "body": payload,
            }, indent=2, ensure_ascii=False),
            name="Request",
            attachment_type=allure.attachment_type.JSON,
        )
        allure.attach(
            json.dumps({
                "status_code": r.status_code,
                "body": r.text,
            }, indent=2, ensure_ascii=False),
            name="Response",
            attachment_type=allure.attachment_type.JSON,
        )

        assert_status_code(r, case.get("expected_status", 200))

        expected = case.get("expected_fields")
        if expected:
            _assert_expected_fields(r, expected, case.get("id", "case"))


async def _run_steps(client, auth_header, case, steps) -> None:
    """Multi-step full-flow path: sequential requests with placeholder resolution."""
    case_id = case.get("id", "case")
    role = case.get("role") or "admin"
    headers = {}
    logged_in = False
    ctx: dict = {}

    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not step.get("method") or not step.get("path"):
            pytest.fail(f"[{case_id}] step {i}/{len(steps)} must have method and path")

        method = str(step["method"]).upper()
        path = _resolve_placeholders(step["path"], ctx, case_id)
        payload = _resolve_placeholders(step.get("payload") or {}, ctx, case_id)
        expected = step.get("expected_status", 200)

        with allure.step(f"Step {i}/{len(steps)}: {method} {path}"):
            if case.get("auth") == "Y" and not logged_in:
                if role == "admin":
                    headers = dict(auth_header or {})
                else:
                    headers = await _auth_for_role(client, role)
                logged_in = True

            r = await client.request(method, path, headers=headers, json=payload)
            _attach_step_allure(case_id, i, len(steps), method, path, payload, expected, r)
            assert_status_code(r, expected)

            expected_fields = step.get("expected_fields")
            if expected_fields:
                _assert_expected_fields(r, expected_fields, case_id, step_no=i)

            ctx[f"step{i}"] = {"status": r.status_code, "body": r.json()}


async def run_case(client, auth_header, case):
    """Execute a single test case.

    Args:
        client: httpx.AsyncClient or similar with .request()
        auth_header: dict with Authorization header (fallback for admin role)
        case: dict with either
            - method, path, payload, expected_status, expected_fields, role (single step), or
            - steps: list of {method, path, payload, expected_status, expected_fields}
              with {{stepN.status}} / {{stepN.body.<path>}} placeholders (full flow)
    """
    _attach_allure_meta(case)

    steps = case.get("steps")
    if steps:
        await _run_steps(client, auth_header, case, steps)
    else:
        await _run_single(client, auth_header, case)
