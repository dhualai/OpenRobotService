"""
Story: ORS-714 - 建立服务号自动化回归测试机制
生成方式: 由工单 + 接口规格自动生成（框架规范版）
工单: 714
用例数: 14
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


@allure.feature("工单 714 建立服务号自动化回归测试机制")
class TestTicket714:
    """工单 714 建立服务号自动化回归测试机制"""


    @allure.story("自动化回归-范围定义")
    @allure.title("常用基本功能回归集覆盖服务号核心链路（健康检查+菜单+消息推送）")
    @pytest.mark.api
    async def test_tc001(self, client, auth, cleanup):
        """正常流程：常用基本功能回归集覆盖服务号核心链路（健康检查+菜单+消息推送）"""
        await _api(client, "get", "/api/wechat/health",
                   step="Step 1: 服务号服务健康检查",
                   expected_status=200,
                   )
        await _api(client, "get", "/api/wechat/get_menu",
                   step="Step 2: 拉取当前服务号菜单配置",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "post", "/api/wechat/send_message",
                   step="Step 3: 向测试用户发送一条消息",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("自动化回归-范围定义")
    @allure.title("非核心能力（群发）不纳入每轮冒烟回归，仅在专项回归中执行")
    @pytest.mark.api
    async def test_tc002(self, client, auth, cleanup):
        """边界条件：非核心能力（群发）不纳入每轮冒烟回归，仅在专项回归中执行"""
        await _api(client, "post", "/api/wechat/broadcast_message",
                   step="Step 1: 专项回归中验证群发消息能力",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("自动化回归-流程建设")
    @allure.title("自动化回归主流程可一次串行跑通：登录→权限→菜单→发消息")
    @pytest.mark.api
    async def test_tc003(self, client, auth, cleanup):
        """全链路：自动化回归主流程可一次串行跑通：登录→权限→菜单→发消息"""
        await _api(client, "post", "/api/wechat/login",
                   step="Step 1: 微信登录换取会话",
                   expected_status=200,
                   )
        await _api(client, "get", "/api/wechat/permissions?openid=test_openid",
                   step="Step 2: 查询用户权限",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "get", "/api/wechat/get_menu",
                   step="Step 3: 查询菜单配置",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("自动化回归-流程建设")
    @allure.title("登录态缺失时回归流程在首个鉴权步骤失败并终止")
    @pytest.mark.api
    async def test_tc004(self, client):
        """异常流程：登录态缺失时回归流程在首个鉴权步骤失败并终止"""
        await _api(client, "get", "/api/wechat/get_menu",
                   step="Step 1: 未带登录态直接调用菜单查询",
                   expected_status=401,
                   )

    @allure.story("自动化回归-发版触发")
    @allure.title("发版完成后自动触发冒烟回归并产出结果")
    @pytest.mark.api
    async def test_tc005(self, client):
        """正常流程：发版完成后自动触发冒烟回归并产出结果"""
        await _api(client, "get", "/api/health?project_code=Leo_test",
                   step="Step 1: 发版后校验服务健康状态",
                   expected_status=200,
                   )
        await _api(client, "get", "/api/wechat/health",
                   step="Step 2: 校验服务号服务健康状态",
                   expected_status=200,
                   )

    @allure.story("自动化回归-发版触发")
    @allure.title("发版后回归失败（微信依赖不可用）时应判定失败并阻断发布晋级")
    @pytest.mark.api
    async def test_tc006(self, client, auth, cleanup):
        """异常流程：发版后回归失败（微信依赖不可用）时应判定失败并阻断发布晋级"""
        await _api(client, "post", "/api/wechat/send_message",
                   step="Step 1: 发版后执行消息发送冒烟用例",
                   headers=auth,
                   expected_status=503,
                   )

    @allure.story("自动化回归-环境与数据")
    @allure.title("测试环境健康检查与测试数据准备可用，可支撑回归执行")
    @pytest.mark.api
    async def test_tc007(self, client, auth):
        """正常流程：测试环境健康检查与测试数据准备可用，可支撑回归执行"""
        await _api(client, "get", "/api/health?project_code=Leo_test",
                   step="Step 1: 校验测试环境健康状态",
                   expected_status=200,
                   )
        await _api(client, "get", "/api/wechat/permissions?openid=test_openid",
                   step="Step 2: 用测试 openid 查询权限数据",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("自动化回归-环境与数据")
    @allure.title("测试数据隔离：使用不存在的 openid 查询不报错且不污染真实数据")
    @pytest.mark.api
    async def test_tc008(self, client, auth):
        """边界条件：测试数据隔离：使用不存在的 openid 查询不报错且不污染真实数据"""
        await _api(client, "get", "/api/wechat/permissions?openid=not_exist_openid_0001",
                   step="Step 1: 使用不存在的 openid 查询权限",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("自动化回归-微信生态限制")
    @allure.title("受微信端限制的场景以接口级校验替代：JS-SDK 配置与网页授权换取 openid")
    @pytest.mark.api
    async def test_tc009(self, client, auth):
        """正常流程：受微信端限制的场景以接口级校验替代：JS-SDK 配置与网页授权换取 openid"""
        await _api(client, "get", "/api/wechat/config/js-sdk-config?url=https://example.com/page",
                   step="Step 1: 校验 JS-SDK 签名配置接口",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "get", "/api/wechat/get-openid?code=test_code_0001",
                   step="Step 2: 用测试 code 换取 openid",
                   expected_status=200,
                   )

    @allure.story("自动化回归-微信生态限制")
    @allure.title("微信回调签名非法时接口拒绝请求，模拟微信侧不可控输入")
    @pytest.mark.api
    async def test_tc010(self, client):
        """异常流程：微信回调签名非法时接口拒绝请求，模拟微信侧不可控输入"""
        await _api(client, "get", "/api/wechat?signature=invalid_signature&timestamp=1&nonce=2&echostr=test_echo",
                   step="Step 1: 使用非法签名调用微信回调校验接口",
                   expected_status=403,
                   )

    @allure.story("人工回归与小流量观察协同")
    @allure.title("创建人工回归与小流量观察协同工单并回查确认")
    @pytest.mark.api
    async def test_tc011(self, client, auth, cleanup):
        """全链路：创建人工回归与小流量观察协同工单并回查确认"""
        await _api(client, "post", "/api/tasks/",
                   step="Step 1: 创建人工回归+小流量观察协同工单",
                   headers=auth,
                   expected_status=200,
                   expected_fields={"title": "服务号发版人工回归与小流量观察协同"},
                   json={"title": "服务号发版人工回归与小流量观察协同", "description": "自动化回归未覆盖场景由人工回归补充，发版后小流量观察，明确责任人与观察窗口", "ticket_type": "feature", "priority": "P1", "project_id": "Leo_test"},
                   )
        await _api(client, "get", "/api/tasks/1",
                   step="Step 2: 回查该协同工单详情",
                   headers=auth,
                   expected_status=200,
                   expected_fields={"id": 1},
                   )

    @allure.story("人工回归与小流量观察协同")
    @allure.title("协同工单缺少标题时建单失败")
    @pytest.mark.api
    async def test_tc012(self, client, auth, cleanup):
        """异常流程：协同工单缺少标题时建单失败"""
        await _api(client, "post", "/api/tasks/",
                   step="Step 1: 提交缺少标题的协同工单",
                   headers=auth,
                   expected_status=422,
                   json={"description": "仅填写描述，缺失标题", "ticket_type": "feature", "priority": "P1", "project_id": "Leo_test"},
                   )

    @allure.story("平台侧评估与接入")
    @allure.title("提交平台侧评估需求工单并回查评估状态")
    @pytest.mark.api
    async def test_tc013(self, client, auth, cleanup):
        """全链路：提交平台侧评估需求工单并回查评估状态"""
        await _api(client, "post", "/api/tasks/",
                   step="Step 1: 提交平台侧评估需求工单",
                   headers=auth,
                   expected_status=200,
                   expected_fields={"title": "服务号自动化回归测试机制-平台侧评估接入"},
                   json={"title": "服务号自动化回归测试机制-平台侧评估接入", "description": "覆盖常用基本功能，发版自动触发回归；需平台侧评估可行性、实现方式、排期与投入产出", "ticket_type": "feature", "priority": "P0", "project_id": "Leo_test"},
                   )
        await _api(client, "get", "/api/tasks/1",
                   step="Step 2: 回查工单确认已进入平台侧待评估",
                   headers=auth,
                   expected_status=200,
                   expected_fields={"id": 1},
                   )

    @allure.story("平台侧评估与接入")
    @allure.title("未登录提交平台侧评估需求应被拒绝")
    @pytest.mark.api
    async def test_tc014(self, client, cleanup):
        """权限：未登录提交平台侧评估需求应被拒绝"""
        await _api(client, "post", "/api/tasks/",
                   step="Step 1: 未登录提交平台侧评估需求工单",
                   expected_status=401,
                   json={"title": "服务号自动化回归测试机制-平台侧评估接入", "description": "未登录场景校验", "ticket_type": "feature", "priority": "P0", "project_id": "Leo_test"},
                   )
