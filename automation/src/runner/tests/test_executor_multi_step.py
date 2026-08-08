"""Tests for multi-step (full-flow) executor support."""

import httpx
import pytest
from _pytest.outcomes import Failed

from automation.src.mocks.backend_mock import _make_token, create_mock_transport
from automation.src.runner import run_case

AUTH_ADMIN = {"Authorization": f"Bearer {_make_token('testadmin')}"}


def _client():
    return httpx.AsyncClient(transport=create_mock_transport(), base_url="http://test")


async def test_multi_step_full_flow_create_to_status_to_detail():
    """建单 → 改状态 → 查详情,占位符引用前步响应."""
    case = {
        "id": "FLOW-001",
        "auth": "Y",
        "role": "admin",
        "steps": [
            {"method": "POST", "path": "/api/tasks",
             "payload": {"title": "链路测试单", "description": "flow demo"},
             "expected_status": 200},
            {"method": "PATCH", "path": "/api/tasks/{{step1.body.id}}/status",
             "payload": {"status": "in_progress"}, "expected_status": 200},
            {"method": "GET", "path": "/api/tasks/{{step1.body.id}}",
             "expected_status": 200, "expected_fields": {"status": "in_progress"}},
        ],
    }
    async with _client() as client:
        await run_case(client, AUTH_ADMIN, case)


async def test_multi_step_placeholder_keeps_int_type():
    """整串占位符 {{step1.body.id}} 保持 int 类型,payload 可继续引用."""
    case = {
        "id": "FLOW-002",
        "auth": "Y",
        "role": "admin",
        "steps": [
            {"method": "POST", "path": "/api/tasks",
             "payload": {"title": "类型测试", "description": "x"}, "expected_status": 200},
            {"method": "PATCH", "path": "/api/tasks/{{step1.body.id}}/status",
             "payload": {"status": "cancelled"}, "expected_status": 200,
             "expected_fields": {"status": "cancelled"}},
        ],
    }
    async with _client() as client:
        await run_case(client, AUTH_ADMIN, case)


async def test_multi_step_auth_for_non_admin_role():
    """非 admin 角色:登录一次,后续步骤复用 token."""
    case = {
        "id": "FLOW-003",
        "auth": "Y",
        "role": "engineer",
        "steps": [
            {"method": "POST", "path": "/api/tasks",
             "payload": {"title": "工程师建单", "description": "x"}, "expected_status": 200},
            {"method": "GET", "path": "/api/tasks/{{step1.body.id}}",
             "expected_status": 200, "expected_fields": {"created_by": "engineer"}},
        ],
    }
    async with _client() as client:
        await run_case(client, AUTH_ADMIN, case)


async def test_multi_step_placeholder_missing_step_fails():
    """引用未执行的步骤 → 明确报错."""
    case = {
        "id": "FLOW-004",
        "auth": "N",
        "steps": [
            {"method": "GET", "path": "/api/tasks/{{step9.body.id}}", "expected_status": 200},
        ],
    }
    async with _client() as client:
        with pytest.raises(Failed, match="step9 not executed"):
            await run_case(client, AUTH_ADMIN, case)


async def test_multi_step_placeholder_missing_field_fails():
    """引用响应中不存在的字段 → 明确报错."""
    case = {
        "id": "FLOW-005",
        "auth": "N",
        "steps": [
            {"method": "GET", "path": "/health", "expected_status": 200},
            {"method": "GET", "path": "/api/tasks/{{step1.body.no_such_field}}",
             "expected_status": 200},
        ],
    }
    async with _client() as client:
        with pytest.raises(Failed, match="no field"):
            await run_case(client, AUTH_ADMIN, case)


async def test_multi_step_assert_status_failure_reports_step():
    """中间步骤断言失败 → 失败并带 step 信息."""
    case = {
        "id": "FLOW-006",
        "auth": "Y",
        "role": "admin",
        "steps": [
            {"method": "POST", "path": "/api/tasks",
             "payload": {"title": "非法流转", "description": "x"}, "expected_status": 200},
            {"method": "PATCH", "path": "/api/tasks/{{step1.body.id}}/status",
             "payload": {"status": "closed"}, "expected_status": 200},
        ],
    }
    async with _client() as client:
        with pytest.raises(Failed):
            await run_case(client, AUTH_ADMIN, case)


async def test_step_missing_method_or_path_fails():
    """步骤缺 method/path → 明确报错."""
    case = {"id": "FLOW-007", "auth": "N", "steps": [{"path": "/health"}]}
    async with _client() as client:
        with pytest.raises(Failed, match="must have method and path"):
            await run_case(client, AUTH_ADMIN, case)


async def test_single_step_case_still_works_without_steps_key():
    """无 steps 的用例走原单请求路径(回归保护)."""
    case = {
        "id": "FLOW-008",
        "method": "GET",
        "path": "/health",
        "auth": "N",
        "payload": {},
        "expected_status": 200,
    }
    async with _client() as client:
        await run_case(client, AUTH_ADMIN, case)
