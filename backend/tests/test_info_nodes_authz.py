"""项目信息树写接口的鉴权口径（2026-09-20 变更：谁才能改这棵树）。

口径（与 app/modules/admin/api/info_nodes.py 顶部注释同一份）：
  结构类写接口  POST /projects/{id}（增补节点）、/projects/{id}/import（整树导入）、
                /projects/{id}/parse-file（AI 识别预览）、
                /projects/{id}/reset-to-template（一键清空：删增补节点 + 清全部已填值）、
                PUT /nodes/{id}、PATCH /nodes/{id}/move、DELETE /nodes/{id}
                → 「该项目下的人」（user_project_roles 里该项目有任一角色）或 admin。
                  节点级路由的项目不在路径上，按节点反查归属项目；全局字段
                  （project_id 为空）不强拦，放行给 Service 层回
                  「全局字段定义请在「详情模板」里修改」——那里给出的原因才是真的。
  详情模板      GET/POST /template → 模板权限码（全局角色 开发者/超级管理员派生）或 admin
  值写入        PUT /nodes/{id}/value → 仍是「登录即可」，本次没动
  增补信息      POST /projects/{id}/custom-nodes → 仍是「登录即可」，本次没动
                （它也会给这棵树加节点，与 /projects/{id} 同性质却更松——是本次改动
                 范围外的既有口径，留了用例钉住；要收紧只需挂 require_project_member）

Service 层一律打桩：这里只测「谁被拦、谁被放行」，落库行为由其它用例覆盖。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.admin.api.auth import get_current_active_user_from_token
from app.modules.admin.api.info_nodes import info_node_router
from app.services.permission_service import PERM_PROJECT_INFO_TEMPLATE, PermissionService
from app.modules.admin.services.info_node_service import info_node_service

# 默认调用者：普通登录用户，哪个项目下都没有角色
CALLER = {"id": "u-1", "username": "bob", "name": "Bob", "permissions": [], "roles": {}}
# 他是这些项目下的人（user_project_roles 里有行）
MEMBER_OF = {"P1"}

GATE_DENIED = "只有该项目下的人员可以编辑项目信息树"
TEMPLATE_ONLY = "全局字段定义请在「详情模板」里修改"


@pytest.fixture
def caller():
    """测试里改这个 dict 就是改「当前登录用户」（admin / 模板权限码都走这里）。"""
    return dict(CALLER)


@pytest.fixture
def member_calls():
    """记录 is_project_member 被问了哪些项目（全局字段那条要确认闸门根本没问）。"""
    return []


@pytest.fixture
def svc(monkeypatch):
    """Service 层全打桩：闸门之外一律不碰库。"""
    mocks = SimpleNamespace(
        get_node=MagicMock(return_value=None),
        get_tree=MagicMock(return_value=[]),
        add_custom_node=MagicMock(return_value={"id": "n-new", "node_name": "新标签"}),
        update_node=MagicMock(return_value={"id": "n-1", "node_name": "改名了"}),
        move_node=MagicMock(return_value={"id": "n-1"}),
        delete_node=MagicMock(return_value=True),
        import_tree=MagicMock(return_value=2),
        reset_to_template=MagicMock(return_value={"cleared": 3, "nodes_removed": 2}),
        set_value=MagicMock(return_value={"id": "n-1", "value": "x"}),
    )
    for name, mock in vars(mocks).items():
        monkeypatch.setattr(info_node_service, name, mock)
    return mocks


@pytest.fixture
def client(caller, member_calls, svc, monkeypatch):
    app = FastAPI()
    app.include_router(info_node_router, prefix="/api/admin")
    app.dependency_overrides[get_current_active_user_from_token] = lambda: caller

    def fake_is_member(user_id, project_id):
        member_calls.append((user_id, project_id))
        return project_id in MEMBER_OF

    monkeypatch.setattr(PermissionService, "is_project_member", staticmethod(fake_is_member))
    with TestClient(app) as test_client:
        yield test_client


def _node_in(project_id, node_id="n-1"):
    return {"id": node_id, "project_id": project_id, "node_name": "订单信息", "value_type": "text"}


# ── 项目成员：结构类接口放行 ─────────────────────────────

def test_project_member_can_add_node_and_import_tree(client, svc):
    created = client.post("/api/admin/info-nodes/projects/P1", json={"title": "新标签"})
    assert created.status_code == 201
    assert svc.add_custom_node.call_args[0][0] == "P1"

    imported = client.post("/api/admin/info-nodes/projects/P1/import", json={"nodes": [{"title": "A"}]})
    assert imported.status_code == 200
    assert imported.json() == {"imported": 2}

    cleared = client.post("/api/admin/info-nodes/projects/P1/reset-to-template")  # 恢复成模板：删增补节点 + 清全部已填值
    assert cleared.status_code == 200
    assert cleared.json() == {"cleared": 3, "nodes_removed": 2}


def test_project_member_can_drive_node_level_routes(client, svc):
    svc.get_node.return_value = _node_in("P1")
    assert client.put("/api/admin/info-nodes/nodes/n-1", json={"title": "改名了"}).status_code == 200
    assert client.patch("/api/admin/info-nodes/nodes/n-1/move", json={"new_sort_order": 1}).status_code == 200
    assert client.delete("/api/admin/info-nodes/nodes/n-1").status_code == 200


def test_admin_passes_even_without_project_role(client, svc, caller):
    caller["permissions"] = ["admin"]
    svc.get_node.return_value = _node_in("P9")          # 不在 P9 下，但他是 admin
    assert client.post("/api/admin/info-nodes/projects/P9", json={"title": "X"}).status_code == 201
    assert client.put("/api/admin/info-nodes/nodes/n-1", json={"title": "X"}).status_code == 200


# ── 不是这个项目的人：结构类接口全拦 ─────────────────────

def test_non_member_is_denied_on_every_structural_route(client, svc):
    svc.get_node.return_value = _node_in("P2")          # P2 的节点，bob 只属于 P1
    for response in (
        client.post("/api/admin/info-nodes/projects/P2", json={"title": "新标签"}),
        client.post("/api/admin/info-nodes/projects/P2/import", json={"nodes": []}),
        client.post("/api/admin/info-nodes/projects/P2/reset-to-template"),
        client.put("/api/admin/info-nodes/nodes/n-1", json={"title": "改名"}),
        client.patch("/api/admin/info-nodes/nodes/n-1/move", json={"new_sort_order": 0}),
        client.delete("/api/admin/info-nodes/nodes/n-1"),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == GATE_DENIED
    # 拦在闸门上：Service 一次都没被调到
    svc.add_custom_node.assert_not_called()
    svc.update_node.assert_not_called()
    svc.move_node.assert_not_called()
    svc.delete_node.assert_not_called()
    svc.import_tree.assert_not_called()
    svc.reset_to_template.assert_not_called()


def test_parse_file_is_gated_like_the_rest_of_the_import_flow(client, svc):
    """AI 识别也要读整棵树 + 烧模型配额，与落库同门槛。"""
    denied = client.post(
        "/api/admin/info-nodes/projects/P2/parse-file",
        files={"file": ("需求.docx", b"x", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == GATE_DENIED


def test_node_route_404_when_node_is_gone(client, svc):
    svc.get_node.return_value = None
    assert client.put("/api/admin/info-nodes/nodes/n-x", json={"title": "改名"}).status_code == 404


# ── 全局字段：闸门放行，由 Service 给出真正的原因 ────────────

def test_global_node_is_not_blocked_by_the_gate(client, svc, member_calls):
    """全局字段不属于任何项目：闸门不该拿「越权」搪塞，真正的原因归 Service 说。"""
    svc.get_node.return_value = {"id": "g-1", "project_id": None, "node_name": "基础信息"}
    svc.update_node.side_effect = PermissionError(TEMPLATE_ONLY)

    response = client.put("/api/admin/info-nodes/nodes/g-1", json={"title": "改名"})
    assert response.status_code == 403
    assert response.json()["detail"] == TEMPLATE_ONLY
    # 闸门没去问「他是不是这个项目的人」——全局字段压根没有归属项目
    assert member_calls == []
    svc.update_node.assert_called_once()


# ── 本次没动的那两条：值写入 / 增补信息仍是「登录即可」 ─────────

def test_value_write_stays_open_to_any_logged_in_user(client, svc):
    """填值不改结构，任何登录用户都能写（本次改动范围外）。"""
    response = client.put("/api/admin/info-nodes/nodes/n-1/value?project_id=P9", json={"value": "中力"})
    assert response.status_code == 200
    assert svc.set_value.call_args[0][:2] == ("P9", "n-1")


def test_custom_nodes_endpoint_is_still_login_only(client, svc):
    """增补信息没跟着收紧（既有口径），留着这条用例是为了将来收紧时改它而不是改坏它。"""
    response = client.post(
        "/api/admin/info-nodes/projects/P9/custom-nodes",
        json={"parent_id": "n-1", "title": "现场联系人"},
    )
    assert response.status_code == 201
    assert svc.add_custom_node.call_args[0][0] == "P9"


# ── 详情模板：全局角色 开发者 / 超级管理员 或 admin ────────────

def test_template_needs_the_derived_permission_code(client, caller):
    denied = client.get("/api/admin/info-nodes/template")
    assert denied.status_code == 403

    caller["permissions"] = [PERM_PROJECT_INFO_TEMPLATE]     # 后端按全局角色名派生后下发
    assert client.get("/api/admin/info-nodes/template").status_code == 200

    caller["permissions"] = []                               # 项目成员（哪怕项目再多）也不行
    caller["roles"] = {"P1": ["role-x"]}
    assert client.post("/api/admin/info-nodes/template", json={"nodes": []}).status_code == 403


def test_template_admin_still_passes(client, caller):
    caller["permissions"] = ["admin"]
    assert client.get("/api/admin/info-nodes/template").status_code == 200
