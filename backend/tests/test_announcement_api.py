"""公告接口口径（活动中心）。

被测（app/modules/admin/api/announcement.py）：

  前台（登录即可，无后台权限）
    - GET  /api/announcements/active   首页固定公告条：当前生效的一条
    - GET  /api/announcements          抽屉内已发布公告列表

  后台（需 frontend:admin:announcement:manage）
    - GET/POST   /api/admin/announcements
    - PUT/DELETE /api/admin/announcements/{id}
    - POST       /api/admin/announcements/{id}/publish | /offline

一律不连真库：conftest 已把 app.core.database 换成 MagicMock；这里 override
get_db 返回可编排的 Mock。权限码注册只应发生在 permissions.get_all_permissions
的注册块里——接口路径里写 permissions 表会直接让本文件用例失败（回归钉）。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth_routes import get_current_active_user_from_token as core_auth_dep
# 后台权限依赖绑的是 admin/api/auth.py 里自定义的同名函数，不是 core 的版本
from app.modules.admin.api.auth import get_current_active_user_from_token as admin_auth_dep
from app.services.identity_service import IdentityService
from app.modules.admin.api import announcement as ann_api

USER = {"id": "u-bob", "username": "bob", "permissions": [], "roles": {}}
ADMIN = {"id": "u-adm", "username": "admin", "permissions": ["admin"], "roles": {}}


class _Res:
    def __init__(self, rows=None, scalar=None, one=None):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._one = one

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._one


def _ann(**over):
    data = {
        "id": 1,
        "title": "机器人知识红包活动上线",
        "content": "第一行\n第二行",
        "is_pinned": True,
        "status": "published",
        "published_at": datetime(2026, 10, 15, 10, 0, 0),
        "created_by": "admin",
        "created_at": datetime(2026, 10, 15, 9, 0, 0),
        "updated_at": datetime(2026, 10, 15, 9, 0, 0),
    }
    data.update(over)
    return SimpleNamespace(**data)


@pytest.fixture(autouse=True)
def _forbid_inline_permission_write(monkeypatch):
    """回归钉：接口路径不允许写 permissions 表。

    原实现把 ensure_announcement_permission() 放在各后台接口函数体首行，而
    require_permission 是依赖、先于函数体执行 → 没有该权限的账号永远 403，
    注册那行永远走不到（生产自锁）。这里把它设成硬失败，防止有人再加回去。
    """
    def _boom(*args, **kwargs):
        raise AssertionError(
            "接口路径不应注册权限码（应在 permissions.get_all_permissions 的注册块里）"
        )

    monkeypatch.setattr(IdentityService, "add_permission", _boom)


@pytest.fixture
def db():
    mock = MagicMock()
    mock.commit = AsyncMock()
    mock.refresh = AsyncMock()
    return mock


@pytest.fixture
def make_client(db):
    clients = []

    def _make(user):
        app = FastAPI()
        # router 自带 prefix="/announcements"，这里只补网关前缀（与 app/__init__.py 一致）
        app.include_router(ann_api.frontend_router, prefix="/api")
        app.include_router(ann_api.router, prefix="/api/admin")

        async def override_db():
            yield db

        app.dependency_overrides[ann_api.get_db] = override_db
        app.dependency_overrides[core_auth_dep] = lambda: user
        app.dependency_overrides[admin_auth_dep] = lambda: user
        clients.append(TestClient(app))
        return clients[-1]

    yield _make
    for c in clients:
        c.close()


# ---------------------------------------------------------------------------
# 前台：首页公告条只常驻一条
# ---------------------------------------------------------------------------

def test_active_returns_single_announcement(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(rows=[_ann()])])

    body = make_client(USER).get("/api/announcements/active").json()

    assert body["item"]["title"] == "机器人知识红包活动上线"
    assert body["item"]["is_pinned"] is True
    assert body["item"]["content"] == "第一行\n第二行"


def test_active_returns_null_when_nothing_published(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(rows=[])])

    body = make_client(USER).get("/api/announcements/active").json()

    assert body["item"] is None


def test_frontend_list_returns_total_and_items(make_client, db):
    db.execute = AsyncMock(side_effect=[
        _Res(scalar=2),
        _Res(rows=[_ann(id=1), _ann(id=2, title="第二篇", is_pinned=False)]),
    ])

    body = make_client(USER).get("/api/announcements").json()

    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [1, 2]
    assert body["items"][1]["is_pinned"] is False


# ---------------------------------------------------------------------------
# 后台：鉴权与状态机
# ---------------------------------------------------------------------------

def test_admin_list_forbidden_for_plain_user(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(scalar=0), _Res(rows=[])])

    response = make_client(USER).get("/api/admin/announcements")

    assert response.status_code == 403


def test_admin_list_allowed_for_admin(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(scalar=1), _Res(rows=[_ann()])])

    body = make_client(ADMIN).get("/api/admin/announcements").json()

    assert body["total"] == 1
    assert body["items"][0]["status"] == "published"


def test_create_rejects_unknown_status(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res()])

    response = make_client(ADMIN).post(
        "/api/admin/announcements",
        json={"title": "标题", "content": "正文", "status": "nonsense"},
    )

    assert response.status_code == 400


def test_create_published_stamps_published_at(make_client, db):
    response = make_client(ADMIN).post(
        "/api/admin/announcements",
        json={"title": "新公告", "content": "正文", "status": "published"},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["status"] == "published"
    assert item["published_at"] is not None


def test_create_draft_has_no_published_at(make_client, db):
    body = make_client(ADMIN).post(
        "/api/admin/announcements",
        json={"title": "草稿", "content": "正文", "status": "draft"},
    ).json()

    assert body["item"]["status"] == "draft"
    assert body["item"]["published_at"] is None


def test_publish_and_offline_transition(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(rows=[_ann(status="draft", published_at=None)])])

    body = make_client(ADMIN).post("/api/admin/announcements/1/publish").json()

    assert body["item"]["status"] == "published"
    assert body["item"]["published_at"] is not None


def test_offline_transition(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(rows=[_ann(status="published")])])

    body = make_client(ADMIN).post("/api/admin/announcements/1/offline").json()

    assert body["item"]["status"] == "offline"
    # 下线不清空发布时间，便于后台看出「原本什么时候发的」
    assert body["item"]["published_at"] is not None


def test_update_returns_404_when_missing(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(rows=[])])

    response = make_client(ADMIN).put(
        "/api/admin/announcements/404", json={"title": "改个名"}
    )

    assert response.status_code == 404


def test_delete_returns_id(make_client, db):
    db.execute = AsyncMock(side_effect=[_Res(rows=[_ann()])])
    db.delete = AsyncMock()

    body = make_client(ADMIN).delete("/api/admin/announcements/1").json()

    assert body == {"ok": True, "id": 1}
    db.delete.assert_awaited_once()


def test_admin_api_allowed_with_derived_permission(make_client, db):
    """超管不是靠 admin 账号直通，而是登录态 permissions 里带了这个码。"""
    db.execute = AsyncMock(side_effect=[_Res(scalar=0), _Res(rows=[])])
    super_admin = {
        "id": "u-sa",
        "username": "bob",
        "permissions": [ann_api.PERM_CODE],
        "roles": {},
    }

    response = make_client(super_admin).get("/api/admin/announcements")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 权限码注册：只能走 permissions 的注册块，且超管默认可见
# ---------------------------------------------------------------------------

def test_super_admin_role_derives_announcement_permission():
    """全局角色「超级管理员」默认拿到公告管理码（否则后台入口对超管不可见）。"""
    from app.services.permission_service import (
        _GLOBAL_ROLE_DERIVED_PERMISSIONS,
        PERM_ANNOUNCEMENT,
    )

    assert PERM_ANNOUNCEMENT == ann_api.PERM_CODE
    assert PERM_ANNOUNCEMENT in _GLOBAL_ROLE_DERIVED_PERMISSIONS["超级管理员"]


def test_ensure_registers_code_for_permission_management(monkeypatch):
    """权限管理页要能勾到该码：注册时写入的 code 必须与接口闸门一致。"""
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(IdentityService, "add_permission", _capture)

    ann_api.ensure_announcement_permission()

    assert captured["code"] == ann_api.PERM_CODE
    assert captured["permission_id"] == "perm_frontend_admin_announcement_manage"
