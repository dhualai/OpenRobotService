from typing import Dict, List, Optional, Any
import json

from app.core.db import (
    engine,
    SessionLocal,
    async_engine,
    AsyncSessionLocal,
    get_db,
    get_async_db,
)
from app.models.base import Base
from app.models.identity import (
    Role,
    Permission,
    UserDB,
    role_permissions,
    user_project_roles,
)
from app.models.delivery import Project
from app.services.identity_service import identity_service
from app.services.permission_service import permission_service
from app.core.config import settings

Base.metadata.create_all(bind=engine)


class DatabaseManager:
    def add_user(self, user_id: str, username: str, hashed_password: str, permissions: List[str],
                 name: Optional[str] = None, status: str = "inactive", external_credentials: Optional[Dict[str, Dict[str, str]]] = None) -> bool:
        return identity_service.add_user(user_id, username, hashed_password, permissions, name, status, external_credentials)

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        return identity_service.get_user(username)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        return identity_service.get_user_by_id(user_id)

    def update_user(self, user_id: str, **kwargs) -> bool:
        return identity_service.update_user(user_id, **kwargs)

    def delete_user(self, user_id: str) -> bool:
        return identity_service.delete_user(user_id)

    def add_role(self, role_id: str, role_name: str) -> bool:
        return identity_service.add_role(role_id, role_name)

    def get_role(self, role_id: str) -> Optional[Dict[str, str]]:
        return identity_service.get_role(role_id)

    def get_all_roles(self) -> List[Dict[str, str]]:
        return identity_service.get_all_roles()

    def delete_role(self, role_id: str) -> bool:
        return identity_service.delete_role(role_id)

    def get_all_projects(self) -> List[Dict[str, str]]:
        return identity_service.get_all_projects()

    def get_project(self, project_id: str) -> Optional[Dict[str, str]]:
        return identity_service.get_project(project_id)

    def add_project(self, project_id: str, project_code: str, project_name: str) -> bool:
        return identity_service.add_project(project_id, project_code, project_name)

    def update_project(self, project_id: str, project_name: str) -> bool:
        return identity_service.update_project(project_id, project_name)

    def delete_project(self, project_id: str) -> bool:
        return identity_service.delete_project(project_id)

    def add_role_permission(self, role_permission_id: str, role_id: str, permission_id: str) -> bool:
        return identity_service.add_role_permission(role_permission_id, role_id, permission_id)

    def remove_role_permission(self, role_id: str, permission_id: str) -> bool:
        return identity_service.remove_role_permission(role_id, permission_id)

    def get_role_permissions(self, role_id: str) -> List[Dict[str, Any]]:
        return permission_service.get_role_permissions(role_id)

    def check_role_permission(self, role_id: str, permission_id: str) -> bool:
        return permission_service.check_role_permission(role_id, permission_id)

    def add_user_project_role(self, user_project_role_id: str, user_id: str, project_id: str, role_id: str, report_to_id: Optional[str] = None) -> bool:
        return identity_service.add_user_project_role(user_project_role_id, user_id, project_id, role_id, report_to_id)

    def batch_add_user_project_roles(self, roles_data: List[Dict[str, str]]) -> int:
        return identity_service.batch_add_user_project_roles(roles_data)

    def remove_user_project_role(self, user_id: str, project_id: str, role_id: str) -> bool:
        return identity_service.remove_user_project_role(user_id, project_id, role_id)

    def get_user_roles_by_project(self, user_id: str, project_id: str) -> List[str]:
        return permission_service.get_user_roles_by_project(user_id, project_id)

    def get_user_roles_all_projects(self, user_id: str) -> Dict[str, List[str]]:
        return permission_service.get_user_roles_all_projects(user_id)

    def get_all_users_roles_all_projects(self, user_ids: List[str]) -> Dict[str, Dict[str, List[str]]]:
        return permission_service.get_all_users_roles_all_projects(user_ids)

    def get_user_report_to(self, user_id: str, project_id: str) -> Optional[str]:
        return permission_service.get_user_report_to(user_id, project_id)

    def get_all_reporters(self, username: str, project_id: str) -> List[Dict[str, Any]]:
        return permission_service.get_all_reporters(username, project_id)

    def get_users_by_role(self, role_id: str) -> List[Dict[str, str]]:
        return identity_service.get_users_by_role(role_id)

    def get_permission(self, permission_id: str) -> Optional[Dict[str, Any]]:
        return permission_service.get_permission(permission_id)

    def get_all_permissions(self) -> List[Dict[str, Any]]:
        return identity_service.get_all_permissions()

    def get_user_permissions(self, user_id: str, project_id: str) -> List[Dict[str, Any]]:
        return permission_service.get_user_permissions(user_id, project_id)

    def add_permission(self, permission_id: str, code: str, name: str, resource_type: str, action: str, description: Optional[str] = None) -> bool:
        return identity_service.add_permission(permission_id, code, name, resource_type, action, description)

    def delete_permission(self, permission_id: str) -> bool:
        return identity_service.delete_permission(permission_id)

    def update_permission(self, permission_id: str, **kwargs) -> bool:
        return identity_service.update_permission(permission_id, **kwargs)

    def get_project_members(self, project_id: str, include_usp: bool = False) -> List[Dict[str, Any]]:
        return identity_service.get_project_members(project_id, include_usp)

    def get_db(self):
        return SessionLocal()


db_manager = DatabaseManager()


def init_users_db():
    from app.core.security import get_password_hash
    admin_id = "user_admin"
    admin_username = "admin"
    admin_password = "123456"
    admin_permissions = ["admin", "permissions:*", "users:*"]

    if not db_manager.get_user(admin_username):
        db_manager.add_user(
            user_id=admin_id,
            username=admin_username,
            hashed_password=get_password_hash(admin_password),
            permissions=admin_permissions
        )


def get_projects_from_db() -> Dict[str, str]:
    projects = {}
    all_projects = db_manager.get_all_projects()
    for project in all_projects:
        projects[project['id']] = project['name']

    return projects


def get_user_with_roles(username: str) -> Optional[Dict[str, Any]]:
    return permission_service.get_user_with_roles(username)