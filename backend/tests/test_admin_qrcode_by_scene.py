"""按场景值查二维码接口（摇人页「扫码进入」链路）的口径。

被测：`GET /api/admin/qrcodes/by-scene/{scene}`（见 app/modules/admin/api/qrcode.py）。

口径（2026-09-30：scene 改为 str(id)、project_id 列已删）：
  - 鉴权「登录即可」——用 app.core.auth_routes.get_current_active_user_from_token，
    而不是同文件其它接口的 require_permission("frontend:admin:other:show")：
    摇人页是 C 端，普通客服没有后台权限。注意 admin/api/auth.py 里另有一个同名函数
    （admin 侧自己的实现），两者不是同一个可调用对象，所以这里 override 的是 core 那个。
  - scene 即 str(id)：纯数字，按主键精确查；非数字 → 400（不再是宽松的 1~64 字符白名单）。
  - 命中 → 200，录入信息行自带的 项目名/客户名/车型 原样回吐；响应不再有 project_id 键
    （项目id 就是行 id，由 id/scene_str 表达）。
  - 未命中 → 404。
  - 双段路径不与单段 GET /{qid} 冲突（同 /stats/summary 先例）。

一律不连真库：conftest 已把 app.core.database 换成 MagicMock，这里只替换
qrcode 模块自己的 db_manager，返回一个可编排的假 Session。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth_routes import get_current_active_user_from_token
from app.models.wechat_qrcode import QrcodeStatus
from app.modules.admin.api import qrcode as qrcode_api
from app.modules.admin.api.qrcode import router as qrcode_router

# 普通登录用户：没有任何后台权限，用于钉住「登录即可」这个口径
LOGIN_USER = {"id": "u-1", "username": "bob", "name": "Bob", "permissions": [], "roles": {}}

ROW_ID = 9
SCENE = str(ROW_ID)  # scene = str(id)（2026-09-30 口径）


def _row(**over):
    """一条录入信息行的完整属性集（覆盖 _to_dict 会读到的每个字段）。"""
    fields = {
        "id": ROW_ID,
        "scene_str": SCENE,
        "name": "项目A",
        "description": None,
        "ticket": None,
        "url": None,
        "qrcode_image_url": None,
        "type": "permanent",
        "expire_seconds": None,
        "status": QrcodeStatus.PUBLISHED,
        "batch_id": None,
        "project_name": "项目A",
        "project_code": "P-0001",
        "project_location": "上海",
        "customer_name": "客户A",
        "vehicle_model": "XQE",
        "redirect_url": None,
        "created_by": None,
        "published_by": None,
        "deprecated_by": None,
        "ticket_created_at": None,
        "created_at": None,
        "updated_at": None,
    }
    fields.update(over)
    return SimpleNamespace(**fields)


@pytest.fixture
def db(monkeypatch):
    """假 Session：默认「查无此码」，用例按需覆盖 first() 的返回。"""
    fake = MagicMock()
    fake.query.return_value.filter.return_value.first.return_value = None
    fake.query.return_value.filter.return_value.all.return_value = []
    monkeypatch.setattr(qrcode_api, "db_manager", SimpleNamespace(get_db=lambda: fake))
    return fake


@pytest.fixture
def app(db):
    application = FastAPI()
    application.include_router(qrcode_router, prefix="/api/admin")
    return application


@pytest.fixture
def client(app):
    """已登录（覆盖 core 的登录依赖）。"""
    app.dependency_overrides[get_current_active_user_from_token] = lambda: dict(LOGIN_USER)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client(app):
    """未登录：不覆盖依赖，走真实实现（无 token 时它在读库之前就抛 401）。"""
    with TestClient(app) as test_client:
        yield test_client


def test_published_row_returns_project_customer_model(client, db):
    """命中即 200：弹窗要的三个字段一次拿全，状态原样带出，且不带 project_id 键。"""
    db.query.return_value.filter.return_value.first.return_value = _row()

    response = client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 200
    body = response.json()
    assert body["scene_str"] == SCENE
    assert body["status"] == QrcodeStatus.PUBLISHED
    assert body["project_name"] == "项目A"
    assert body["customer_name"] == "客户A"
    assert body["vehicle_model"] == "XQE"
    assert body["project_code"] == "P-0001"
    # 项目id 就是行 id（str(id)），响应不再有单独的 project_id 键
    assert "project_id" not in body


def test_scene_parsed_to_row_id(client, db):
    """scene 是数字字符串：解析成 int 当主键查（str(id) 口径）。"""
    db.query.return_value.filter.return_value.first.return_value = _row()

    response = client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 200
    assert response.json()["id"] == ROW_ID


def test_unknown_scene_returns_404(client, db):
    """查无此码（数字但无对应行）→ 404（前端据此静默降级，不打扰用户）。"""
    response = client.get("/api/admin/qrcodes/by-scene/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "二维码不存在"


def test_non_numeric_scene_rejected_before_touching_db(client, db):
    """非数字 scene（旧 proj_ 值/乱填）直接 400，且不进库。"""
    response = client.get("/api/admin/qrcodes/by-scene/proj_notexist")

    assert response.status_code == 400
    assert response.json()["detail"] == "scene_str 必须是数字（= str(id)）"
    db.query.assert_not_called()


def test_requires_login(anon_client):
    """未登录被拦，不因为它是「摇人页用的接口」就免鉴权。"""
    response = anon_client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 401


def test_not_gated_by_admin_permission(client, db):
    """普通登录用户（无任何后台权限）也要能查到——这正是与同文件其它接口的区别。"""
    db.query.return_value.filter.return_value.first.return_value = _row(status=QrcodeStatus.INIT)

    response = client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 200
    assert response.json()["status"] == QrcodeStatus.INIT


def test_institute_member_overwrites_published_project_info(client, db, app):
    app.dependency_overrides[get_current_active_user_from_token] = lambda: {
        **LOGIN_USER, "department": "智能规划研究院",
    }
    row = _row()
    first = db.query.return_value.filter.return_value.first
    first.side_effect = [row, ("P-0002",)]
    payload = {
        "project_code": "P-0002",
        "project_name": "项目B",
        "project_location": "杭州",
        "customer_name": "客户B",
        "vehicle_model": "XCD",
    }

    response = client.put(f"/api/admin/qrcodes/{ROW_ID}/project-info", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ROW_ID
    assert body["scene_str"] == SCENE
    assert body["status"] == QrcodeStatus.PUBLISHED
    for field, value in payload.items():
        assert body[field] == value
        assert getattr(row, field) == value
    assert row.name == payload["project_name"]
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(row)
    db.add.assert_not_called()

    first.side_effect = None
    first.return_value = row
    refreshed = client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert refreshed.status_code == 200
    for field, value in payload.items():
        assert refreshed.json()[field] == value


@pytest.mark.parametrize("path", [f"by-scene/{SCENE}", str(ROW_ID)])
@pytest.mark.parametrize(("auth", "can_edit"), [
    ({}, False),
    ({"department": None}, False),
    ({"department": ""}, False),
    ({"department": "智能规划研究院"}, True),
    ({"department": "实施服务部"}, False),
    ({"department": "智能规划研究院分院"}, False),
    ({"department": "智能规划研究院（外包）"}, False),
    ({"department": "实施服务部", "roles": {"P-0001": ["r-dispatch"]}}, False),
    ({"department": "实施服务部", "roles": {"global": ["r-dev"]}}, False),
    ({"department": "实施服务部", "permissions": ["admin"]}, False),
    ({"permissions": ["admin"]}, False),
])
def test_qrcode_returns_department_based_edit_permission(client, db, app, path, auth, can_edit):
    app.dependency_overrides[get_current_active_user_from_token] = lambda: {**LOGIN_USER, **auth}
    db.query.return_value.filter.return_value.first.return_value = _row()

    response = client.get(f"/api/admin/qrcodes/{path}")

    assert response.status_code == 200
    assert response.json()["can_edit_project_info"] is can_edit


@pytest.mark.parametrize("auth", [
    {},
    {"department": None},
    {"department": ""},
    {"department": "实施服务部"},
    {"department": "智能规划研究院分院"},
    {"department": "实施服务部", "roles": {"P-0001": ["r-dispatch"]}},
    {"department": "实施服务部", "roles": {"global": ["r-dev"]}},
    {"department": "实施服务部", "permissions": ["admin"]},
])
def test_unauthorized_published_update_is_rejected_before_changes(client, db, app, auth):
    app.dependency_overrides[get_current_active_user_from_token] = lambda: {**LOGIN_USER, **auth}
    row = _row()
    db.query.return_value.filter.return_value.first.return_value = row

    response = client.put(f"/api/admin/qrcodes/{ROW_ID}/project-info", json={
        "project_code": "OTHER",
        "project_name": "不允许的修改",
        "customer_name": "不允许的客户",
        "department": "智能规划研究院",
    })

    assert response.status_code == 403
    assert response.json()["detail"] == "仅智能规划研究院的用户可修改已发布信息"
    assert row.project_code == "P-0001"
    assert row.project_name == "项目A"
    assert row.customer_name == "客户A"
    db.commit.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize("auth", [
    {"department": "智能规划研究院"},
    {"department": "智能规划研究院", "roles": {"global": ["r-dev"]}},
    {"department": "智能规划研究院", "permissions": ["admin"]},
])
def test_institute_member_can_update_regardless_of_role(client, db, app, auth):
    app.dependency_overrides[get_current_active_user_from_token] = lambda: {**LOGIN_USER, **auth}
    row = _row()
    db.query.return_value.filter.return_value.first.return_value = row

    response = client.put(f"/api/admin/qrcodes/{ROW_ID}/project-info", json={"customer_name": "新客户"})

    assert response.status_code == 200
    assert row.customer_name == "新客户"
    db.commit.assert_called_once()


def test_ordinary_user_can_still_fill_unpublished_info(client, db):
    row = _row(status=QrcodeStatus.ENTERING)
    db.query.return_value.filter.return_value.first.return_value = row

    response = client.put(f"/api/admin/qrcodes/{ROW_ID}/project-info", json={"customer_name": "首次录入"})

    assert response.status_code == 200
    assert row.customer_name == "首次录入"
    assert row.status == QrcodeStatus.PUBLISHED
    db.commit.assert_called_once()
