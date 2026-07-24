from typing import Dict, List, Optional, Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.db import SessionLocal
from app.models import UserDB, Role, Permission, Project, role_permissions, user_project_roles


class PermissionService:
    @staticmethod
    def _get_db() -> Session:
        db = SessionLocal()
        try:
            return db
        except:
            db.close()
            raise

    @staticmethod
    def get_user_roles_all_projects(user_id: str) -> Dict[str, List[str]]:
        db = PermissionService._get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.user_id == user_id
            )).fetchall()
            
            roles_by_project = {}
            for role in roles:
                project_id = role.project_id or 'global'
                if project_id not in roles_by_project:
                    roles_by_project[project_id] = []
                roles_by_project[project_id].append(role.role_id)
            
            return roles_by_project
        finally:
            db.close()

    @staticmethod
    def get_user_roles_by_project(user_id: str, project_id: str) -> List[str]:
        db = PermissionService._get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                (user_project_roles.c.user_id == user_id) &
                (user_project_roles.c.project_id == project_id)
            )).fetchall()
            
            return [role.role_id for role in roles]
        finally:
            db.close()

    @staticmethod
    def get_all_users_roles_all_projects(user_ids: List[str]) -> Dict[str, Dict[str, List[str]]]:
        db = PermissionService._get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.user_id.in_(user_ids)
            )).fetchall()
            
            all_roles = {}
            for role in roles:
                if role.user_id not in all_roles:
                    all_roles[role.user_id] = {}
                project_id = role.project_id or 'global'
                if project_id not in all_roles[role.user_id]:
                    all_roles[role.user_id][project_id] = []
                all_roles[role.user_id][project_id].append(role.role_id)
            
            for user_id in user_ids:
                if user_id not in all_roles:
                    all_roles[user_id] = {}
            
            return all_roles
        finally:
            db.close()

    @staticmethod
    def get_role_permissions(role_id: str) -> List[Dict[str, Any]]:
        db = PermissionService._get_db()
        try:
            permissions = db.execute(role_permissions.select().where(
                role_permissions.c.role_id == role_id
            )).fetchall()
            
            return [{
                'id': perm.id,
                'role_id': perm.role_id,
                'permission_id': perm.permission_id
            } for perm in permissions]
        finally:
            db.close()

    @staticmethod
    def get_permission(permission_id: str) -> Optional[Dict[str, Any]]:
        db = PermissionService._get_db()
        try:
            permission = db.query(Permission).filter(Permission.id == permission_id).first()
            if permission:
                return {
                    'id': permission.id,
                    'code': permission.code,
                    'name': permission.name,
                    'description': permission.description,
                    'resource_type': permission.resource_type,
                    'action': permission.action,
                    'enabled': permission.enabled == "true"
                }
            return None
        finally:
            db.close()

    @staticmethod
    def get_user_report_to(user_id: str, project_id: str) -> Optional[str]:
        db = PermissionService._get_db()
        try:
            result = db.execute(user_project_roles.select().where(
                (user_project_roles.c.user_id == user_id) &
                (user_project_roles.c.project_id == project_id)
            )).first()
            
            if result and hasattr(result, 'report_to_id'):
                return result.report_to_id
            return None
        finally:
            db.close()

    @staticmethod
    def get_all_reporters(username: str, project_id: str) -> List[Dict[str, Any]]:
        db = PermissionService._get_db()
        try:
            user = db.query(UserDB).filter(UserDB.username == username).first()
            if not user:
                return []
            
            reporters = []
            current_user_id = user.id
            level = 1
            
            while current_user_id:
                result = db.execute(user_project_roles.select().where(
                    (user_project_roles.c.user_id == current_user_id) &
                    (user_project_roles.c.project_id == project_id)
                )).first()
                
                if not result or not getattr(result, 'report_to_id', None):
                    break
                
                report_to_user = db.query(UserDB).filter(UserDB.id == result.report_to_id).first()
                if report_to_user:
                    reporters.append({
                        'username': report_to_user.username,
                        'name': report_to_user.name,
                        'level': level
                    })
                    current_user_id = report_to_user.id
                    level += 1
                else:
                    break
            
            return reporters
        finally:
            db.close()

    @staticmethod
    def check_role_permission(role_id: str, permission_id: str) -> bool:
        db = PermissionService._get_db()
        try:
            result = db.execute(role_permissions.select().where(
                (role_permissions.c.role_id == role_id) &
                (role_permissions.c.permission_id == permission_id)
            )).first()
            return result is not None
        except Exception:
            return False
        finally:
            db.close()

    @staticmethod
    def get_user_permissions(user_id: str, project_id: str) -> List[Dict[str, Any]]:
        user_roles = PermissionService.get_user_roles_by_project(user_id, project_id)
        
        user_permissions = []
        for role_id in user_roles:
            role_permissions_list = PermissionService.get_role_permissions(role_id)
            for perm in role_permissions_list:
                permission_detail = PermissionService.get_permission(perm['permission_id'])
                if permission_detail:
                    user_permissions.append(permission_detail)
        
        return user_permissions

    @staticmethod
    def get_user_with_roles(username: str) -> Optional[Dict[str, Any]]:
        db = PermissionService._get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.username == username).first()
            if not db_user:
                return None
            
            import json
            external_credentials = {}
            if hasattr(db_user, 'external_credentials') and db_user.external_credentials:
                try:
                    external_credentials = json.loads(db_user.external_credentials)
                except:
                    external_credentials = {}
            
            user_roles = PermissionService.get_user_roles_all_projects(db_user.id)
            
            all_permissions = set(["admin"]) if db_user.username == 'admin' else set(["user"])
            
            project_permissions_dict = {}
            
            role_permissions_map = {}
            permission_details_map = {}
            
            all_role_ids = set()
            for role_ids in user_roles.values():
                all_role_ids.update(role_ids)
            
            if all_role_ids:
                stmt = select(role_permissions).where(role_permissions.c.role_id.in_(all_role_ids))
                role_permissions_results = db.execute(stmt).fetchall()
                
                for result in role_permissions_results:
                    result_dict = result._asdict() if hasattr(result, '_asdict') else dict(result)
                    role_id = result_dict['role_id']
                    permission_id = result_dict['permission_id']
                    if role_id not in role_permissions_map:
                        role_permissions_map[role_id] = []
                    role_permissions_map[role_id].append(permission_id)
                
                all_permission_ids = set()
                for perm_ids in role_permissions_map.values():
                    all_permission_ids.update(perm_ids)
                
                if all_permission_ids:
                    permissions_results = db.query(Permission).filter(Permission.id.in_(all_permission_ids)).all()
                    
                    for perm in permissions_results:
                        permission_details_map[perm.id] = {
                            'id': perm.id,
                            'code': perm.code,
                            'name': perm.name,
                            'description': perm.description,
                            'resource_type': perm.resource_type,
                            'action': perm.action
                        }
            
            for project_id, role_ids in user_roles.items():
                project_permissions = []
                
                for role_id in role_ids:
                    if role_id in role_permissions_map:
                        for perm_id in role_permissions_map[role_id]:
                            if perm_id in permission_details_map:
                                project_permissions.append(permission_details_map[perm_id])
                
                for perm in project_permissions:
                    all_permissions.add(perm['code'])
                
                resource_perms = {}
                for perm in project_permissions:
                    code = perm['code']
                    resource_type = code.split(':')[0]
                    if resource_type not in resource_perms:
                        resource_perms[resource_type] = []
                    resource_perms[resource_type].append(code)
                
                if resource_perms:
                    project_permissions_dict[project_id] = resource_perms
            
            return {
                'id': db_user.id,
                'username': db_user.username,
                'password_hash': db_user.password_hash,
                'permissions': list(all_permissions),
                'roles': user_roles,
                'projectPermissions': project_permissions_dict,
                'name': getattr(db_user, 'name', None),
                'status': getattr(db_user, 'status', 'inactive'),
                'external_credentials': external_credentials
            }
        finally:
            db.close()


permission_service = PermissionService()