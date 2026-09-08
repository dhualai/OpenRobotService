from typing import Dict, List, Optional, Any
import time
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.db import SessionLocal
from app.models import UserDB, Role, Permission, Project, role_permissions, user_project_roles
from app.models.organization import Company, Department

# get_user_with_roles 结果的 TTL 缓存（秒）。权限/角色变更不频繁，60s 内复用可显著
# 减少每请求的多次 DB 查询；变更后最多 60s 生效（用户重新登录可立即生效）。
_USER_ROLES_CACHE_TTL = 60
_user_roles_cache: Dict[str, Dict[str, Any]] = {}
_user_roles_cache_ts: Dict[str, float] = {}


def invalidate_user_roles_cache(username: Optional[str] = None) -> None:
    """清除用户角色缓存。不传 username 时清空全部（权限/角色变更后调用）。"""
    if username is None:
        _user_roles_cache.clear()
        _user_roles_cache_ts.clear()
    else:
        _user_roles_cache.pop(username, None)
        _user_roles_cache_ts.pop(username, None)


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
    def get_all_users_project_role_relations(user_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """批量返回用户的项目角色关系（含 report_to_id），供前端构建汇报树。"""
        db = PermissionService._get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.user_id.in_(user_ids)
            )).fetchall()
            relations: Dict[str, List[Dict[str, Any]]] = {}
            for role in roles:
                relations.setdefault(role.user_id, []).append({
                    'project_id': role.project_id or 'global',
                    'role_id': role.role_id,
                    'report_to_id': getattr(role, 'report_to_id', None),
                })
            for user_id in user_ids:
                relations.setdefault(user_id, [])
            return relations
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
        # TTL 缓存命中检查
        cached = _user_roles_cache.get(username)
        if cached is not None:
            ts = _user_roles_cache_ts.get(username, 0)
            if time.time() - ts < _USER_ROLES_CACHE_TTL:
                return cached

        db = PermissionService._get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.username == username).first()
            if not db_user:
                db_user = db.query(UserDB).filter(UserDB.id == username).first()
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
            
            rm = getattr(db_user, 'responsibility_modules', None)
            # 历史脏数据（非 dict）归一为 {}，避免 Pydantic 校验失败
            if not isinstance(rm, dict):
                rm = {}
            # 通过 company_id/department_id join 主数据表获取名称
            company_name = None
            department_name = None
            cid = getattr(db_user, 'company_id', None)
            did = getattr(db_user, 'department_id', None)
            if cid:
                comp = db.query(Company).filter(Company.id == cid).first()
                if comp:
                    company_name = comp.name
            if did:
                dept = db.query(Department).filter(Department.id == did).first()
                if dept:
                    department_name = dept.name
            result = {
                'id': db_user.id,
                'username': db_user.username,
                'password_hash': db_user.password_hash,
                'permissions': list(all_permissions),
                'roles': user_roles,
                'projectPermissions': project_permissions_dict,
                'name': getattr(db_user, 'name', None),
                'status': getattr(db_user, 'status', 'inactive'),
                'external_credentials': external_credentials,
                'avatar_resource_id': getattr(db_user, 'avatar_resource_id', None),
                'company_id': cid,
                'department_id': did,
                'company': company_name,
                'department': department_name,
                'responsibility_modules': rm,
                'job_level': getattr(db_user, 'job_level', 1) or 1,
                'duty_text': getattr(db_user, 'duty_text', None),
                'supervisor_id': getattr(db_user, 'supervisor_id', None),
            }
            # 写入 TTL 缓存（仅缓存查到用户的结果；None 不缓存以便用户创建后立即可查）
            _user_roles_cache[username] = result
            _user_roles_cache_ts[username] = time.time()
            return result
        finally:
            db.close()


permission_service = PermissionService()