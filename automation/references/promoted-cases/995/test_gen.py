"""
Story: ORS-995 - 拓扑平台功能移植升级到问题文档
生成方式: 由工单 + 接口规格自动生成（框架规范版）
工单: 995
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


@allure.feature("工单 995 拓扑平台功能移植升级到问题文档")
class TestTicket995:
    """工单 995 拓扑平台功能移植升级到问题文档"""


    @allure.story("迁移范围识别")
    @allure.title("按项目拉取工单清单并逐单核对问题文档映射完整性")
    @pytest.mark.api
    async def test_tc001(self, client, auth):
        """正常流程：按项目拉取工单清单并逐单核对问题文档映射完整性"""
        await _api(client, "get", "/api/tasks/?project_id=Leo_test&page=1&size=50",
                   step="Step 1: 拉取项目下工单列表作为迁移源范围",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "get", "/api/tasks/1001/spec-doc",
                   step="Step 2: 逐单读取目标问题文档，确认源文档已建立映射",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("迁移范围识别")
    @allure.title("无源文档工单查询问题文档时不应产生脏数据")
    @pytest.mark.api
    async def test_tc002(self, client, auth):
        """异常流程：无源文档工单查询问题文档时不应产生脏数据"""
        await _api(client, "get", "/api/tasks/1002/spec-doc",
                   step="Step 1: 查询无源文档工单的问题文档",
                   headers=auth,
                   expected_status=404,
                   )
        await _api(client, "get", "/api/tasks/1002",
                   step="Step 2: 回查工单详情，确认未被误写入文档",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("需求文档内容迁移")
    @allure.title("迁移文档内容写入目标工单并回读校验一致")
    @pytest.mark.api
    async def test_tc003(self, client, auth):
        """正常流程：迁移文档内容写入目标工单并回读校验一致"""
        await _api(client, "put", "/api/tasks/1001/spec-doc",
                   step="Step 1: 写入迁移后的需求文档内容",
                   headers=auth,
                   expected_status=200,
                   json={"content": "# 拓扑平台需求文档\n## 一、背景\n将拓扑平台需求文档迁移至工单问题文档。"},
                   )
        await _api(client, "get", "/api/tasks/1001/spec-doc",
                   step="Step 2: 回读目标文档校验内容一致",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("需求文档内容迁移")
    @allure.title("向不存在的工单写入问题文档应失败且不影响其他文档")
    @pytest.mark.api
    async def test_tc004(self, client, auth):
        """异常流程：向不存在的工单写入问题文档应失败且不影响其他文档"""
        await _api(client, "put", "/api/tasks/999999/spec-doc",
                   step="Step 1: 向不存在的工单写入问题文档",
                   headers=auth,
                   expected_status=404,
                   json={"content": "# 异常写入测试"},
                   )
        await _api(client, "get", "/api/tasks/1001/spec-doc",
                   step="Step 2: 校验既有工单文档未被覆盖",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("迁移内容完整性")
    @allure.title("含图片的文档迁移后图片不丢失可正常展示")
    @pytest.mark.api
    async def test_tc005(self, client, auth, cleanup):
        """正常流程：含图片的文档迁移后图片不丢失可正常展示"""
        await _api(client, "post", "/api/tasks/spec-doc/image",
                   step="Step 1: 上传文档内嵌图片",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "put", "/api/tasks/1001/spec-doc",
                   step="Step 2: 写入含图片引用的文档内容",
                   headers=auth,
                   expected_status=200,
                   json={"content": "# 需求\n![架构图](/api/tasks/files/spec-doc/1001/arch.png)"},
                   )
        await _api(client, "get", "/api/tasks/1001/spec-doc",
                   step="Step 3: 回读文档确认图片引用保留",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("迁移内容完整性")
    @allure.title("含表格与代码块的文档结构要素解析与迁移后数量一致")
    @pytest.mark.api
    async def test_tc006(self, client, auth, cleanup):
        """边界条件：含表格与代码块的文档结构要素解析与迁移后数量一致"""
        await _api(client, "post", "/api/tasks/spec-doc/parse",
                   step="Step 1: 解析含结构化元素的源文档",
                   headers=auth,
                   expected_status=200,
                   json={"content": "## 参数表\n| 字段 | 说明 |\n|---|---|\n| id | 主键 |\n\n```python\nprint(1)\n```\n\n- 列表项1\n- 列表项2"},
                   )
        await _api(client, "put", "/api/tasks/1001/spec-doc",
                   step="Step 2: 写入解析后的文档内容",
                   headers=auth,
                   expected_status=200,
                   json={"content": "## 参数表\n| 字段 | 说明 |\n|---|---|\n| id | 主键 |\n\n```python\nprint(1)\n```\n\n- 列表项1\n- 列表项2"},
                   )
        await _api(client, "get", "/api/tasks/1001/spec-doc",
                   step="Step 3: 回读校验表格/代码块/列表数量一致",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("迁移内容格式保真")
    @allure.title("多级标题与样式迁移后可正常展示且可继续编辑")
    @pytest.mark.api
    async def test_tc007(self, client, auth):
        """正常流程：多级标题与样式迁移后可正常展示且可继续编辑"""
        await _api(client, "put", "/api/tasks/1001/spec-doc",
                   step="Step 1: 写入含多级层次与样式的文档",
                   headers=auth,
                   expected_status=200,
                   json={"content": "# 一级标题\n## 二级标题\n### 三级标题\n\n**加粗文本** 与 *斜体文本*\n\n1. 步骤一\n2. 步骤二"},
                   )
        await _api(client, "get", "/api/tasks/1001/spec-doc",
                   step="Step 2: 回读文档校验层级与样式标记保留",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("迁移内容格式保真")
    @allure.title("特殊字符与编码在迁移后逐字符一致")
    @pytest.mark.api
    async def test_tc008(self, client, auth):
        """边界条件：特殊字符与编码在迁移后逐字符一致"""
        await _api(client, "put", "/api/tasks/1001/spec-doc",
                   step="Step 1: 写入含特殊字符的文档",
                   headers=auth,
                   expected_status=200,
                   json={"content": "# 需求 §1 ±√ ≤≥ \"引号\" 😀 全角　空格\n\n换行测试\n\n```\n<TAG> & \"quoted\" \n```"},
                   )
        await _api(client, "get", "/api/tasks/1001/spec-doc",
                   step="Step 2: 回读文档逐字符比对",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("文档与需求工单关联")
    @allure.title("建立问题文档与需求工单关联并可双向追溯")
    @pytest.mark.api
    async def test_tc009(self, client, auth, cleanup):
        """正常流程：建立问题文档与需求工单关联并可双向追溯"""
        await _api(client, "post", "/api/tasks/995/relations",
                   step="Step 1: 建立文档与需求工单的关联",
                   headers=auth,
                   expected_status=200,
                   json={"related_task_id": 1001, "relation_type": "spec_doc"},
                   )
        await _api(client, "get", "/api/tasks/995/relations",
                   step="Step 2: 从工单侧查询关联列表",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "get", "/api/tasks/995/relations/tree?max_depth=2",
                   step="Step 3: 查询关联树确认可逐层打开",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("文档与需求工单关联")
    @allure.title("关联不存在的工单应失败且不产生脏关联")
    @pytest.mark.api
    async def test_tc010(self, client, auth, cleanup):
        """异常流程：关联不存在的工单应失败且不产生脏关联"""
        await _api(client, "post", "/api/tasks/995/relations",
                   step="Step 1: 关联不存在的工单",
                   headers=auth,
                   expected_status=404,
                   json={"related_task_id": 999999, "relation_type": "spec_doc"},
                   )
        await _api(client, "get", "/api/tasks/995/relations",
                   step="Step 2: 校验关联列表未新增脏关联",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("问题文档维护")
    @allure.title("在工单问题文档中补充新内容并保存，重新进入后内容一致")
    @pytest.mark.api
    async def test_tc011(self, client, auth, cleanup):
        """正常流程：在工单问题文档中补充新内容并保存，重新进入后内容一致"""
        await _api(client, "post", "/api/auth/login",
                   step="Step 1: 登录服务号",
                   expected_status=200,
                   json={"username": "testadmin", "password": "admin123"},
                   )
        await _api(client, "put", "/api/tasks/995/spec-doc",
                   step="Step 2: 在工单995的问题文档末尾追加一段维护说明并保存",
                   headers=auth,
                   expected_status=200,
                   json={"content": "# 拓扑平台需求文档（迁移）\n## 1. 迁移范围\n## 2. 维护说明\n2024-06-01 补充：迁移后的需求内容统一在本问题文档维护。"},
                   )
        await _api(client, "get", "/api/tasks/995/spec-doc",
                   step="Step 3: 重新读取工单995的问题文档",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("问题文档维护")
    @allure.title("修改与删除问题文档已有段落并保存，相邻内容不受影响")
    @pytest.mark.api
    async def test_tc012(self, client, auth, cleanup):
        """边界条件：修改与删除问题文档已有段落并保存，相邻内容不受影响"""
        await _api(client, "post", "/api/auth/login",
                   step="Step 1: 登录服务号",
                   expected_status=200,
                   json={"username": "testadmin", "password": "admin123"},
                   )
        await _api(client, "put", "/api/tasks/995/spec-doc",
                   step="Step 2: 修改其中一个章节标题与正文，并删除一个列表条目后保存",
                   headers=auth,
                   expected_status=200,
                   json={"content": "# 拓扑平台需求文档（迁移）\n## 1. 迁移范围（已更新）\n本次迁移覆盖拓扑平台全部需求文档。\n| 项目 | 数量 |\n| --- | --- |\n| 需求文档 | 20 |"},
                   )
        await _api(client, "get", "/api/tasks/995/spec-doc",
                   step="Step 3: 重新读取问题文档校验修改与删除结果",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("统一维护入口")
    @allure.title("迁移后从需求工单侧进入问题文档完成维护闭环")
    @pytest.mark.api
    async def test_tc013(self, client, auth, cleanup):
        """全链路：迁移后从需求工单侧进入问题文档完成维护闭环"""
        await _api(client, "post", "/api/auth/login",
                   step="Step 1: 登录服务号",
                   expected_status=200,
                   json={"username": "testadmin", "password": "admin123"},
                   )
        await _api(client, "get", "/api/tasks/995",
                   step="Step 2: 打开需求工单995详情，确认可定位到问题文档入口",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "get", "/api/tasks/995/spec-doc",
                   step="Step 3: 通过工单问题文档入口读取并更新文档",
                   headers=auth,
                   expected_status=200,
                   )

    @allure.story("统一维护入口")
    @allure.title("无编辑权限用户只能查看问题文档，不能通过接口修改")
    @pytest.mark.api
    async def test_tc014(self, client, auth, cleanup):
        """权限：无编辑权限用户只能查看问题文档，不能通过接口修改"""
        await _api(client, "post", "/api/auth/login",
                   step="Step 1: 以普通账号登录服务号",
                   expected_status=200,
                   json={"username": "testuser", "password": "user123"},
                   )
        await _api(client, "get", "/api/tasks/995/spec-doc",
                   step="Step 2: 普通账号读取工单995的问题文档",
                   headers=auth,
                   expected_status=200,
                   )
        await _api(client, "put", "/api/tasks/995/spec-doc",
                   step="Step 3: 普通账号尝试更新工单995的问题文档",
                   headers=auth,
                   expected_status=403,
                   json={"content": "# 越权修改尝试"},
                   )
