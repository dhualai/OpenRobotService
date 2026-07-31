from typing import List, Dict, Optional, Any
from app.core.database import db_manager, UserDB
from app.schemas.user import User, UserInDB
from app.services.permission_service import permission_service
from app.models import role_permissions
import json
import time


class UserService:
    _user_cache: Optional[Dict[str, str]] = None
    _cache_timestamp: Optional[float] = None
    _CACHE_EXPIRE_SECONDS = 600

    @classmethod
    def get_user_list(cls, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        db = db_manager.get_db()
        try:
            all_user_records = db.query(UserDB).all()
            paginated_user_records = all_user_records[skip:skip + limit]
            
            result = []
            user_ids = [user_record.id for user_record in paginated_user_records]
            all_users_roles = db_manager.get_all_users_roles_all_projects(user_ids)
            
            for user_record in paginated_user_records:
                user_roles = all_users_roles.get(user_record.id, {})
                
                external_credentials = {}
                if hasattr(user_record, 'external_credentials') and user_record.external_credentials:
                    try:
                        external_credentials = json.loads(user_record.external_credentials)
                    except:
                        external_credentials = {}
                
                user_response = {
                    'id': user_record.id,
                    'username': user_record.username,
                    'permissions': [],
                    'roles': user_roles,
                    'name': getattr(user_record, 'name', None),
                    'status': getattr(user_record, 'status', 'inactive'),
                    'external_credentials': external_credentials,
                    'department': getattr(user_record, 'department', None),
                    'responsibility_modules': getattr(user_record, 'responsibility_modules', None) or {},
                    'job_level': getattr(user_record, 'job_level', 1),
                    'duty_text': getattr(user_record, 'duty_text', None),
                }
                
                result.append(user_response)
            
            return result
        finally:
            db.close()

    @classmethod
    def get_user_detail(cls, username: str) -> Optional[Dict[str, Any]]:
        user_data = db_manager.get_user(username)
        if not user_data:
            return None
        
        user_with_roles = cls._get_user_with_roles(user_data)
        
        return {
            'id': user_with_roles['id'],
            'username': user_with_roles['username'],
            'permissions': user_with_roles['permissions'],
            'roles': user_with_roles['roles'],
            'projectPermissions': user_with_roles.get('projectPermissions', {}),
            'name': user_with_roles.get('name'),
            'status': user_with_roles.get('status', 'inactive'),
            'external_credentials': user_with_roles.get('external_credentials', {}),
            'department': user_with_roles.get('department'),
            'responsibility_modules': user_with_roles.get('responsibility_modules') or {},
            'job_level': user_with_roles.get('job_level', 1),
            'duty_text': user_with_roles.get('duty_text'),
        }

    @classmethod
    def _get_user_with_roles(cls, user_data: Dict[str, Any]) -> Dict[str, Any]:
        user_data['roles'] = db_manager.get_user_roles_all_projects(user_data['id'])
        
        all_permissions = set(user_data.get('permissions', [])) if user_data.get('permissions') else set()
        
        project_permissions_dict = {}
        
        role_permissions_map = {}
        permission_details_map = {}
        
        all_role_ids = set()
        for role_ids in user_data['roles'].values():
            all_role_ids.update(role_ids)
        
        if all_role_ids:
            db = db_manager.get_db()
            try:
                from sqlalchemy import select
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
                    from app.models import Permission
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
            finally:
                db.close()
        
        for project_id, role_ids in user_data['roles'].items():
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
        
        user_data['permissions'] = list(all_permissions)
        user_data['projectPermissions'] = project_permissions_dict

        if 'external_credentials' not in user_data:
            user_data['external_credentials'] = {}

        user_data.setdefault('department', None)
        user_data.setdefault('responsibility_modules', {})
        user_data.setdefault('job_level', 1)
        user_data.setdefault('duty_text', None)

        return user_data

    @classmethod
    def get_user_map(cls) -> Dict[str, str]:
        current_time = time.time()
        
        if (cls._user_cache is not None and 
            cls._cache_timestamp is not None and 
            current_time - cls._cache_timestamp < cls._CACHE_EXPIRE_SECONDS):
            return cls._user_cache
        
        try:
            users = cls.get_user_list()
            user_map = {}
            for user in users:
                username = user.get("username")
                user_id = user.get("id")
                user_name = user.get("name") or user.get("username") or user_id
                
                if user_id:
                    user_map[user_id] = user_name
                if username:
                    user_map[username] = user_name
            
            cls._user_cache = user_map
            cls._cache_timestamp = current_time
        except Exception as e:
            cls._user_cache = {}
            print(f"加载用户信息失败: {str(e)}")
        
        return cls._user_cache

    @classmethod
    def invalidate_cache(cls):
        cls._user_cache = None
        cls._cache_timestamp = None


user_service = UserService()