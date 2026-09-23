"""一键清空（POST /info-nodes/projects/{id}/clear-values）—— 门槛、出参、错误码，不连库。

口径：与结构类写接口**同门槛**（require_project_member，见 info_nodes.py 顶部注释）。
填一个节点的值是「登录即可」，而这里一次抹掉全项目已填的内容——只藏前端按钮拦不住直连。
Service 层一律打桩，落库行为（删哪些行、记哪条历史）由 Service 用例与真库验证覆盖。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.db  # noqa: F401  先建 engine，与其它后端用例同一约定
from app.modules.admin.api.auth import get_current_active_user_from_token
from app.modules.admin.api.info_nodes import info_node_router
from app.modules.admin.services.info_node_service import (  # noqa: F401  engine 依赖在上面
    _is_blank_value,
    info_node_service,
)
from app.services.permission_service import PermissionService

CALLER = {"id": "u-1", "username": "bob", "name": "Bob", "permissions": [], "roles": {}}
MEMBER_OF = {"P1"}
GATE_DENIED = "只有该项目下的人员可以编辑项目信息树"
CLEAR_PATH = "/api/admin/info-nodes/projects/{pid}/clear-values"


class _Client:
    """一个极小的 TestClient 包装：用 with 进入时打上依赖覆盖，退出时还原会员判据。"""

    def __init__(self, caller, svc):
        self.app = FastAPI()
        self.app.include_router(info_node_router, prefix="/api/admin")
        self.app.dependency_overrides[get_current_active_user_from_token] = lambda: caller
        self.svc = svc
        self.client = TestClient(self.app)
        self._original = None

    def _fake_member(self, user_id, project_id):
        return project_id in MEMBER_OF

    def __enter__(self):
        self._original = PermissionService.is_project_member
        PermissionService.is_project_member = staticmethod(self._fake_member)
        self.client.__enter__()
        return self

    def __exit__(self, *exc_info):
        self.client.__exit__(*exc_info)
        PermissionService.is_project_member = self._original


def _service_stub():
    """Service 层打桩：clear_values 是本次唯一会碰库的调用。"""
    stub = SimpleNamespace(clear_values=MagicMock(return_value={"cleared": 7}))
    original = info_node_service.clear_values
    info_node_service.clear_values = stub.clear_values
    return stub, original


def _clear(client, pid="P1"):
    return client.client.post(CLEAR_PATH.format(pid=pid))


# ── 项目成员：放行，并把清空结果与操作人带出来 ──────────

def test_project_member_clears_and_operator_is_recorded():
    stub, original = _service_stub()
    try:
        with _Client(dict(CALLER), stub) as c:
            response = _clear(c)
            assert response.status_code == 200, response.text
            assert response.json() == {"cleared": 7}
            # 无 Authorization 头 → 退回当前登录用户（历史里记的是谁清的）
            assert stub.clear_values.call_args[0][0] == "P1"
            assert stub.clear_values.call_args[1]["operator"] == "bob"
            assert stub.clear_values.call_args[1]["operator_name"] == "Bob"
    finally:
        info_node_service.clear_values = original


def test_admin_passes_even_without_project_role():
    stub, original = _service_stub()
    try:
        admin = dict(CALLER, permissions=["admin"])
        with _Client(admin, stub) as c:
            assert _clear(c, pid="P9").status_code == 200      # P9 不在 MEMBER_OF 里，但他是 admin
            assert stub.clear_values.call_args[0][0] == "P9"
    finally:
        info_node_service.clear_values = original


# ── 不是这个项目的人：拦在闸门上，Service 一次都没被调 ────

def test_non_member_is_denied_and_service_is_not_called():
    stub, original = _service_stub()
    try:
        with _Client(dict(CALLER), stub) as c:
            response = _clear(c, pid="P2")                     # bob 只在 P1 下
            assert response.status_code == 403
            assert response.json()["detail"] == GATE_DENIED
            stub.clear_values.assert_not_called()
    finally:
        info_node_service.clear_values = original


# ── 项目不存在：Service 抛 LookupError → 404（不是 500） ──

def test_missing_project_maps_to_404():
    stub, original = _service_stub()
    stub.clear_values.side_effect = LookupError("项目不存在")
    try:
        with _Client(dict(CALLER), stub) as c:
            response = _clear(c)
            assert response.status_code == 404
            assert response.json()["detail"] == "项目不存在"
    finally:
        info_node_service.clear_values = original


# ── 「空值」判据：清空时据此跳过没填过的节点（不写空转的历史） ──

def test_blank_value_judgement():
    for blank in (None, "", [], {}):
        assert _is_blank_value(blank) is True, blank
    for filled in ("中力", "0", 0, False, ["a"], {"name": "a.pdf", "resource_id": "7"}):
        assert _is_blank_value(filled) is False, filled
