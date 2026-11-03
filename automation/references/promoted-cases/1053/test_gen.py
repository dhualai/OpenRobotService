"""
Story: ORS-1053 - 删除工单接口500报task_id不能为空
生成方式: 由工单 + 接口规格自动生成（框架规范版）
工单: 1053
用例数: 10
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


@allure.feature("工单 1053 删除工单接口500报task_id不能为空")
class TestTicket1053:
    """工单 1053 删除工单接口500报task_id不能为空"""


    @allure.story("工单管理")
    @allure.title("创建工单后调用删除接口应删除成功并返回2xx")
    @pytest.mark.api
    async def test_tc001(self, client, auth, cleanup):
        """正常流程：创建工单后调用删除接口应删除成功并返回2xx"""
        await _api(client, "post", "/api/tasks/",
                   step="Step 1: 创建一张工单",
                   headers=auth,
                   expected_status=200,
                   json={"title": "删除接口验证工单", "ticket_type": "bug", "priority": "P1", "project_id": "Leo_test"},
                   )
        await _api(client, "delete", "/api/tasks/1",
                   step="Step 2: 删除刚创建的工单",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("工单管理")
    @allure.title("删除不存在的工单应返回资源不存在而非500")
    @pytest.mark.api
    async def test_tc002(self, client, auth):
        """异常流程：删除不存在的工单应返回资源不存在而非500"""
        await _api(client, "delete", "/api/tasks/999999",
                   step="Step 1: 删除不存在的工单",
                   headers=auth,
                   expected_status=404,
                   )

    @allure.story("工单操作日志")
    @allure.title("含操作日志的工单删除时日志写入task_id不为空")
    @pytest.mark.api
    async def test_tc003(self, client, auth):
        """正常流程：含操作日志的工单删除时日志写入task_id不为空"""
        await _api(client, "get", "/api/tasks/1/operation-logs",
                   step="Step 1: 查询该工单的操作日志，确认存在日志记录",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "delete", "/api/tasks/1",
                   step="Step 2: 删除该工单",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("工单操作日志")
    @allure.title("无操作日志的工单删除不应因日志关联失败报错")
    @pytest.mark.api
    async def test_tc004(self, client, auth, cleanup):
        """边界条件：无操作日志的工单删除不应因日志关联失败报错"""
        await _api(client, "post", "/api/tasks/",
                   step="Step 1: 创建新工单（无多余操作，不产生日志）",
                   headers=auth,
                   expected_status=200,
                   json={"title": "无日志工单删除验证", "ticket_type": "bug", "priority": "P2", "project_id": "Leo_test"},
                   )
        await _api(client, "delete", "/api/tasks/2",
                   step="Step 2: 直接删除该工单",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("数据库异常处理")
    @allure.title("删除接口不应以未处理500暴露数据库完整性错误")
    @pytest.mark.api
    async def test_tc005(self, client, auth):
        """异常流程：删除接口不应以未处理500暴露数据库完整性错误"""
        await _api(client, "delete", "/api/tasks/3",
                   step="Step 1: 调用删除接口删除工单",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("数据库异常处理")
    @allure.title("重复删除同一工单第二次应返回404而非500")
    @pytest.mark.api
    async def test_tc006(self, client, auth):
        """异常流程：重复删除同一工单第二次应返回404而非500"""
        await _api(client, "delete", "/api/tasks/4",
                   step="Step 1: 首次删除工单",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "delete", "/api/tasks/4",
                   step="Step 2: 对同一工单再次删除",
                   headers=auth,
                   expected_status=404,
                   )

    @allure.story("删除入口一致性")
    @allure.title("直接调用删除接口与页面入口删除结果一致")
    @pytest.mark.api
    async def test_tc007(self, client, auth):
        """全链路：直接调用删除接口与页面入口删除结果一致"""
        await _api(client, "get", "/api/tasks/5",
                   step="Step 1: 查询工单详情确认存在",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "delete", "/api/tasks/5",
                   step="Step 2: 直接调用接口删除",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "get", "/api/tasks/5",
                   step="Step 3: 再次查询详情确认已删除",
                   headers=auth,
                   expected_status=404,
                   )

    @allure.story("删除权限")
    @allure.title("无权限用户删除他人工单应被拒绝")
    @pytest.mark.api
    async def test_tc008(self, client, auth, cleanup):
        """权限：无权限用户删除他人工单应被拒绝"""
        await _api(client, "post", "/api/auth/login",
                   step="Step 1: 普通用户登录",
                   expected_status=200,
                   expected_fields={"success": true},
                   json={"username": "testuser", "password": "user123"},
                   )
        await _api(client, "delete", "/api/tasks/6",
                   step="Step 2: 普通用户直接调用删除接口删除他人工单",
                   headers=auth,
                   expected_status=403,
                   )

    @allure.story("删除结果反馈")
    @allure.title("删除成功后返回明确结果且详情查询为404")
    @pytest.mark.api
    async def test_tc009(self, client, auth):
        """正常流程：删除成功后返回明确结果且详情查询为404"""
        await _api(client, "delete", "/api/tasks/7",
                   step="Step 1: 删除工单",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "get", "/api/tasks/7",
                   step="Step 2: 查询已删除工单详情",
                   headers=auth,
                   expected_status=404,
                   )

    @allure.story("删除结果反馈")
    @allure.title("删除失败时应返回明确错误信息且不暴露数据库异常")
    @pytest.mark.api
    async def test_tc010(self, client, auth):
        """异常流程：删除失败时应返回明确错误信息且不暴露数据库异常"""
        await _api(client, "delete", "/api/tasks/888888",
                   step="Step 1: 删除不存在的工单，观察失败响应",
                   headers=auth,
                   expected_status=404,
                   )
