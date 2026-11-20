from unittest.mock import MagicMock

import pytest
from sqlalchemy import Integer, MetaData, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.identity import UserDB
from app.models.organization import Department
from app.modules.admin.api.qrcode import _can_edit_project_info
from app.services.identity_service import IdentityService
from app.services.permission_service import PermissionService, invalidate_user_roles_cache
from app.services.user_service import UserService


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    metadata = MetaData()
    for name in ('companies', 'departments', 'users', 'roles', 'permissions', 'role_permissions', 'project', 'user_project_roles'):
        Base.metadata.tables[name].to_metadata(metadata)
    for column_name in ('job_level', 'timezone_locked'):
        metadata.tables['users'].c[column_name].type = Integer()
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(IdentityService, '_get_db', staticmethod(factory))
    monkeypatch.setattr(PermissionService, '_get_db', staticmethod(factory))
    monkeypatch.setattr('app.services.identity_service._notify_ai_personnel_reload', MagicMock())
    monkeypatch.setattr(UserService, 'invalidate_cache', MagicMock())
    invalidate_user_roles_cache()
    with factory() as session:
        session.add_all([
            Department(id='institute', name='智能规划研究院', status='approved'),
            Department(id='other', name='实施服务部', status='approved'),
        ])
        session.commit()
    yield factory
    invalidate_user_roles_cache()
    engine.dispose()


@pytest.mark.parametrize(('old_department', 'new_department', 'expected_name', 'can_edit'), [
    ('other', 'institute', '智能规划研究院', True),
    (None, 'institute', '智能规划研究院', True),
    ('institute', 'other', '实施服务部', False),
    ('institute', None, None, False),
])
def test_department_update_immediately_refreshes_cached_qrcode_permission(
    sessions, old_department, new_department, expected_name, can_edit,
):
    with sessions() as session:
        session.add(UserDB(
            id='user-cache', username='cache-viewer', password_hash='test',
            status='active', department_id=old_department,
        ))
        session.commit()

    old_users = {
        login: PermissionService.get_user_with_roles(login)
        for login in ('cache-viewer', 'user-cache')
    }
    for login, old_user in old_users.items():
        assert PermissionService.get_user_with_roles(login) is old_user
        assert old_user['department_id'] == old_department

    assert IdentityService.update_user('user-cache', department_id=new_department)

    for login, old_user in old_users.items():
        user = PermissionService.get_user_with_roles(login)
        assert user is not old_user
        assert user['department_id'] == new_department
        assert user['department'] == expected_name
        assert _can_edit_project_info(user) is can_edit
    UserService.invalidate_cache.assert_called_once()


def test_failed_department_update_keeps_existing_cached_user(sessions, monkeypatch):
    with sessions() as session:
        session.add(UserDB(
            id='user-cache', username='cache-viewer', password_hash='test',
            status='active', department_id='other',
        ))
        session.commit()
    old_user = PermissionService.get_user_with_roles('cache-viewer')
    failing_session = sessions()
    monkeypatch.setattr(failing_session, 'commit', MagicMock(side_effect=RuntimeError('commit failed')))
    monkeypatch.setattr(IdentityService, '_get_db', staticmethod(lambda: failing_session))

    assert not IdentityService.update_user('user-cache', department_id='institute')

    assert PermissionService.get_user_with_roles('cache-viewer') is old_user
    with sessions() as session:
        assert session.get(UserDB, 'user-cache').department_id == 'other'
    UserService.invalidate_cache.assert_not_called()
