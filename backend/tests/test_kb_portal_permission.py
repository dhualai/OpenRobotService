"""智库写权限码（frontend:kb:write）的注册与授权口径。

覆盖三件事：
1. 码值与接口闸门一致（权限管理页要能勾到）；
2. 注册是幂等的，且不会把被禁用的码自动恢复；
3. 注册点必须在 permissions.get_all_permissions 的注册块里——
   放在接口函数体内会被 require_permission 先 403 挡住，造成生产自锁
   （公告权限历史上就这么踩过，见 test_announcement_api.py 的回归钉）。
"""

import inspect

import pytest
from sqlalchemy import Integer, MetaData, create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.identity import Permission
from app.modules.admin.api import permissions as permissions_api
from app.modules.admin.services.kb_portal_permissions import (
    KB_PORTAL_PERMISSION_ID,
    ensure_kb_portal_permission,
)
from app.services.identity_service import IdentityService
from app.services.permission_service import (
    _GLOBAL_ROLE_DERIVED_PERMISSIONS,
    PERM_KB_WRITE,
    invalidate_user_roles_cache,
)


def test_permission_code_value():
    assert PERM_KB_WRITE == "frontend:kb:write"


def test_not_auto_granted_to_any_global_role():
    """智库写权限必须显式勾选，不随任何全局角色白给。"""
    for role, codes in _GLOBAL_ROLE_DERIVED_PERMISSIONS.items():
        assert PERM_KB_WRITE not in codes, f"{role} 不应默认拿到智库写权限"


def test_ensure_registers_code_for_permission_management(monkeypatch):
    """权限管理页要能勾到该码：注册写入的字段必须与接口闸门一致。"""
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(IdentityService, "add_permission", _capture)
    ensure_kb_portal_permission()

    assert captured["code"] == PERM_KB_WRITE
    assert captured["permission_id"] == KB_PORTAL_PERMISSION_ID
    assert captured["name"] == "智库编辑"
    assert captured["resource_type"] == "frontend"
    assert captured["action"] == "write"


def test_registration_is_wired_into_permission_list_block():
    """回归钉：注册点必须在读权限列表的注册块里，不能在接口函数体内。

    放在函数体内时，require_permission 先于函数体执行，没权限的账号永远 403，
    注册那行永远走不到——权限管理页 therefore 勾不到这个码（生产自锁）。
    """
    src = inspect.getsource(permissions_api.get_all_permissions)
    assert "ensure_kb_portal_permission" in src, (
        "ensure_kb_portal_permission() 必须由 get_all_permissions 的注册块调用"
    )


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    metadata = MetaData()
    for name in (
        "companies",
        "departments",
        "users",
        "roles",
        "permissions",
        "role_permissions",
        "project",
        "user_project_roles",
    ):
        Base.metadata.tables[name].to_metadata(metadata)
    # SQLite 编译不了 MySQL 的 TINYINT（users.job_level / timezone_locked 等），
    # 建表前统一换成 Integer。与 test_knowledge_card_visibility 同一处理，
    # 这里写成遍历而非逐个列名，避免以后加列又要补。
    for table in metadata.tables.values():
        for column in table.columns:
            if type(column.type).__name__ == "TINYINT":
                column.type = Integer()
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(IdentityService, "_get_db", staticmethod(factory))
    invalidate_user_roles_cache()
    yield factory


def test_ensure_is_idempotent(sessions):
    ensure_kb_portal_permission()
    ensure_kb_portal_permission()
    with sessions() as session:
        rows = session.scalars(select(Permission).where(Permission.code == PERM_KB_WRITE)).all()
        assert len(rows) == 1
        assert rows[0].id == KB_PORTAL_PERMISSION_ID
        assert rows[0].enabled == "true"


def test_ensure_does_not_restore_disabled(sessions):
    """被管理员禁用后，重新读权限列表不应把它自动恢复。"""
    assert IdentityService.add_permission(
        KB_PORTAL_PERMISSION_ID, PERM_KB_WRITE, "智库编辑", "frontend", "write"
    )
    assert IdentityService.update_permission(KB_PORTAL_PERMISSION_ID, enabled=False)
    ensure_kb_portal_permission()
    with sessions() as session:
        row = session.get(Permission, KB_PORTAL_PERMISSION_ID)
        assert row is not None and row.enabled == "false"
