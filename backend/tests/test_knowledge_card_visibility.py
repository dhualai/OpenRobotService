from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Integer, MetaData, create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.delivery import Project
from app.models.identity import Permission, Role, UserDB, role_permissions, user_project_roles
from app.modules.admin.api import users as users_api
from app.modules.admin.api import permissions as permissions_api
from app.modules.admin.api import roles as roles_api
from app.modules.admin.services.knowledge_permissions import (
    KNOWLEDGE_CONTRIBUTION_PERMISSION_ID, ensure_knowledge_contribution_permission,
)
from app.schemas.user import UserDetail
from app.services.identity_service import IdentityService
from app.services.permission_service import (
    PERM_KNOWLEDGE_CONTRIBUTION, PERM_PROJECT_INFO_TEMPLATE, PERM_PROJECT_LEDGER_IMPORT,
    PermissionService, invalidate_user_roles_cache,
)


ROLE_IDS = {'超级管理员': 'role-91b', '管理员': 'role-72a', '调度研发': 'role-13c',
            '开发者': 'role-64d', '现场工程师': 'role-85e', '项目经理': 'role-26f'}


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    metadata = MetaData()
    for name in ('companies', 'departments', 'users', 'roles', 'permissions', 'role_permissions', 'project', 'user_project_roles'):
        Base.metadata.tables[name].to_metadata(metadata)
    metadata.tables['users'].c.job_level.type = Integer()
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(PermissionService, '_get_db', staticmethod(factory))
    monkeypatch.setattr(IdentityService, '_get_db', staticmethod(factory))
    invalidate_user_roles_cache()
    with factory() as session:
        session.add_all([Role(id=role_id, name=name, role_type='system' if name in ('超级管理员', '管理员', '开发者') else 'project')
                         for name, role_id in ROLE_IDS.items()])
        session.add_all([Project(id=project_id, code=project_id, name=project_id) for project_id in ('project-A', 'project-B')])
        session.commit()
    yield factory
    invalidate_user_roles_cache()
    engine.dispose()


def seed_user(sessions, assignments, username='viewer'):
    with sessions() as session:
        session.add(UserDB(id='user-visibility', username=username, password_hash='test', status='active'))
        session.flush()
        for position, (project_id, role_name) in enumerate(assignments):
            session.execute(user_project_roles.insert().values(
                id=f'assignment-{position}', user_id='user-visibility', project_id=project_id, role_id=ROLE_IDS[role_name]))
        session.commit()


def grant_visibility(role_name):
    ensure_knowledge_contribution_permission()
    assert IdentityService.add_role_permission(
        f'visibility-{ROLE_IDS[role_name]}', ROLE_IDS[role_name], KNOWLEDGE_CONTRIBUTION_PERMISSION_ID)


@pytest.mark.parametrize('assignments', [
    [(None, '超级管理员')], [(None, '管理员')], [('project-A', '调度研发')],
    [(None, '开发者')], [('project-A', '现场工程师')], [('project-A', '项目经理')], [],
])
def test_role_names_without_explicit_permission_do_not_show_card(sessions, assignments):
    seed_user(sessions, assignments)
    ensure_knowledge_contribution_permission()
    user = PermissionService.get_user_with_roles('viewer')
    assert user['can_view_knowledge_contribution'] is False
    assert PERM_KNOWLEDGE_CONTRIBUTION not in user['permissions']


@pytest.mark.parametrize('assignments,granted_role,visible', [
    ([(None, '超级管理员')], '超级管理员', True),
    ([(None, '管理员')], '管理员', True),
    ([(None, '开发者')], '开发者', True),
    ([('project-A', '调度研发')], '调度研发', True),
    ([('project-A', '现场工程师')], '现场工程师', True),
    ([('project-A', '项目经理')], '项目经理', True),
    ([('project-A', '现场工程师'), ('project-B', '调度研发')], '调度研发', True),
    ([(None, '开发者'), ('project-A', '现场工程师')], '现场工程师', True),
    ([('project-A', '现场工程师')], '项目经理', False),
    ([], '管理员', False),
])
def test_visibility_uses_enabled_permission_granted_to_any_actual_role(sessions, assignments, granted_role, visible):
    seed_user(sessions, assignments)
    grant_visibility(granted_role)
    user = PermissionService.get_user_with_roles('viewer')
    assert user['can_view_knowledge_contribution'] is visible
    assert UserDetail(**user).can_view_knowledge_contribution is visible


def test_admin_username_without_explicit_permission_does_not_bypass_visibility(sessions):
    seed_user(sessions, [], username='admin')
    user = PermissionService.get_user_with_roles('admin')
    assert 'admin' in user['permissions']
    assert user['can_view_knowledge_contribution'] is False


@pytest.mark.parametrize('code', ['*', 'admin', 'frontend:admin:*:show', 'frontend:admin:knowledge:*'])
def test_wildcard_permissions_do_not_replace_explicit_card_permission(sessions, code):
    seed_user(sessions, [(None, '开发者')])
    with sessions() as session:
        session.add(Permission(id='wildcard', code=code, name='全部权限', resource_type='frontend', action='show'))
        session.flush()
        session.execute(role_permissions.insert().values(id='role-wildcard', role_id=ROLE_IDS['开发者'], permission_id='wildcard'))
        session.commit()
    user = PermissionService.get_user_with_roles('viewer')
    assert code in user['permissions']
    assert user['can_view_knowledge_contribution'] is False


def test_existing_global_role_derived_permissions_are_preserved(sessions):
    seed_user(sessions, [(None, '超级管理员')])
    user = PermissionService.get_user_with_roles('viewer')
    assert {PERM_PROJECT_INFO_TEMPLATE, PERM_PROJECT_LEDGER_IMPORT} <= set(user['permissions'])
    assert user['can_view_knowledge_contribution'] is False


def test_role_removal_recomputes_visibility_after_cache_invalidation(sessions):
    seed_user(sessions, [(None, '管理员')])
    grant_visibility('管理员')
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True
    with sessions() as session:
        session.execute(delete(user_project_roles).where(user_project_roles.c.user_id == 'user-visibility'))
        session.commit()
    invalidate_user_roles_cache('viewer')
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False


def test_permission_grant_and_removal_invalidate_cached_visibility(sessions):
    seed_user(sessions, [('project-A', '现场工程师')])
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False
    grant_visibility('现场工程师')
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True
    assert IdentityService.remove_role_permission(ROLE_IDS['现场工程师'], KNOWLEDGE_CONTRIBUTION_PERMISSION_ID)
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False


def test_global_permission_switch_overrides_all_granted_roles_and_invalidates_cache(sessions):
    seed_user(sessions, [(None, '超级管理员'), ('project-A', '现场工程师')])
    grant_visibility('超级管理员')
    grant_visibility('现场工程师')
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True
    assert IdentityService.update_permission(KNOWLEDGE_CONTRIBUTION_PERMISSION_ID, enabled=False)
    user = PermissionService.get_user_with_roles('viewer')
    assert user['can_view_knowledge_contribution'] is False
    assert PERM_KNOWLEDGE_CONTRIBUTION not in user['permissions']
    assert IdentityService.update_permission(KNOWLEDGE_CONTRIBUTION_PERMISSION_ID, enabled=True)
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True


def test_permission_registration_is_idempotent_and_does_not_restore_disabled_or_revoked_configuration(sessions):
    seed_user(sessions, [('project-A', '调度研发')])
    grant_visibility('调度研发')
    assert IdentityService.remove_role_permission(ROLE_IDS['调度研发'], KNOWLEDGE_CONTRIBUTION_PERMISSION_ID)
    assert IdentityService.update_permission(KNOWLEDGE_CONTRIBUTION_PERMISSION_ID, enabled=False)
    ensure_knowledge_contribution_permission()
    ensure_knowledge_contribution_permission()
    with sessions() as session:
        rows = session.scalars(select(Permission).where(Permission.code == PERM_KNOWLEDGE_CONTRIBUTION)).all()
        assert len(rows) == 1
        assert rows[0].enabled == 'false'
        assert not session.execute(select(role_permissions)).all()
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False


def test_existing_permission_with_same_code_and_different_id_is_reused(sessions):
    seed_user(sessions, [('project-A', '现场工程师')])
    assert IdentityService.add_permission('existing-card', PERM_KNOWLEDGE_CONTRIBUTION, '原卡片权限', 'frontend', 'show')
    assert IdentityService.add_role_permission('existing-link', ROLE_IDS['现场工程师'], 'existing-card')
    ensure_knowledge_contribution_permission()
    with sessions() as session:
        assert session.get(Permission, KNOWLEDGE_CONTRIBUTION_PERMISSION_ID) is None
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True


def test_permission_deletion_clears_cached_visibility_and_bindings(sessions):
    seed_user(sessions, [(None, '管理员')])
    grant_visibility('管理员')
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True
    assert IdentityService.delete_permission(KNOWLEDGE_CONTRIBUTION_PERMISSION_ID)
    ensure_knowledge_contribution_permission()
    assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False


@pytest.fixture
def permission_app(sessions, monkeypatch):
    manager = SimpleNamespace(
        get_all_permissions=IdentityService.get_all_permissions,
        get_permission=PermissionService.get_permission,
        get_role=IdentityService.get_role,
        check_role_permission=PermissionService.check_role_permission,
        add_role_permission=IdentityService.add_role_permission,
        remove_role_permission=IdentityService.remove_role_permission,
        update_permission=IdentityService.update_permission,
    )
    monkeypatch.setattr(permissions_api, 'db_manager', manager)
    monkeypatch.setattr(roles_api, 'db_manager', manager)
    app = FastAPI()
    app.include_router(permissions_api.router)
    app.include_router(roles_api.router)
    app.dependency_overrides[permissions_api.get_current_active_user_from_token] = lambda: {'permissions': ['admin']}
    return app


def test_permission_management_lists_card_switch_and_role_configuration_controls_visibility(sessions, permission_app):
    seed_user(sessions, [('project-A', '现场工程师')])
    with TestClient(permission_app) as client:
        response = client.get('/permissions/')
        assert response.status_code == 200
        permission = next(item for item in response.json()['data'] if item['code'] == PERM_KNOWLEDGE_CONTRIBUTION)
        assert permission['name'] == '显示知识贡献榜'
        assert permission['enabled'] is True
        assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False
        role_url = f"/roles/{ROLE_IDS['现场工程师']}/permissions"
        payload = {'permission_ids': [permission['id']]}
        assert client.post(role_url, json=payload).status_code == 200
        assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True
        assert client.put(f"/permissions/{permission['id']}", json={'enabled': False}).status_code == 200
        assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False
        response = client.get('/permissions/')
        permission = next(item for item in response.json()['data'] if item['code'] == PERM_KNOWLEDGE_CONTRIBUTION)
        assert permission['enabled'] is False
        assert client.put(f"/permissions/{permission['id']}", json={'enabled': True}).status_code == 200
        assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is True
        assert client.post(f'{role_url}/remove', json=payload).status_code == 200
        assert PermissionService.get_user_with_roles('viewer')['can_view_knowledge_contribution'] is False


def test_unprivileged_user_cannot_initialize_or_configure_card_permission(sessions, permission_app):
    permission_app.dependency_overrides[permissions_api.get_current_active_user_from_token] = lambda: {'permissions': []}
    with TestClient(permission_app) as client:
        assert client.get('/permissions/').status_code == 403
        assert client.put(f'/permissions/{KNOWLEDGE_CONTRIBUTION_PERMISSION_ID}', json={'enabled': False}).status_code == 403
        assert client.post(f"/roles/{ROLE_IDS['现场工程师']}/permissions",
                           json={'permission_ids': [KNOWLEDGE_CONTRIBUTION_PERMISSION_ID]}).status_code == 403
    with sessions() as session:
        assert session.get(Permission, KNOWLEDGE_CONTRIBUTION_PERMISSION_ID) is None


@pytest.mark.parametrize('visible', [True, False, None])
def test_user_detail_api_forwards_visibility_and_defaults_to_hidden(monkeypatch, visible):
    user = {'id': 'viewer', 'username': 'viewer', 'permissions': [], 'roles': {}}
    if visible is not None:
        user['can_view_knowledge_contribution'] = visible
    monkeypatch.setattr(users_api, 'get_user_with_roles', MagicMock(return_value=user))
    app = FastAPI()
    app.include_router(users_api.router)
    app.dependency_overrides[users_api.get_current_active_user_from_token] = lambda: user
    with TestClient(app) as client:
        response = client.get('/users/viewer/detail')
    assert response.status_code == 200
    assert response.json()['can_view_knowledge_contribution'] is (visible is True)
