import asyncio
import uuid
import time
import functools
from typing import Dict, List, Any

async def generate_request_id() -> str:
    return str(uuid.uuid4())

async def get_current_timestamp() -> int:
    return int(time.time() * 1000)

def check_permission(user, resource_type: str, resource_id: str, action: str = "read") -> bool:
    if "admin" in user.permissions or f"{resource_type}:*" in user.permissions:
        return True
    
    permission_str = f"{resource_type}:{resource_id}:{action}"
    return permission_str in user.permissions

def check_indicator_permission(user, indicator_name: str) -> bool:
    return check_permission(user, "indicators", indicator_name, "read")

def check_project_permission(user, project_id: str) -> bool:
    if "admin" in user.permissions or "projects:*" in user.permissions:
        return True
    
    permission_str = f"projects:{project_id}"
    return permission_str in user.permissions

def check_project_indicator_permission(user, project_id: str, indicator_name: str) -> bool:
    if "admin" in user.permissions or "indicators:*" in user.permissions:
        return True
    
    permission_str1 = f"projects:{project_id}:indicators:*"
    permission_str2 = f"projects:{project_id}:indicators:{indicator_name}"
    return permission_str1 in user.permissions or permission_str2 in user.permissions

def get_user_permissions_from_roles(user_id: str, db_manager) -> List[str]:
    permissions = []
    
    user_roles = db_manager.get_user_roles_all_projects(user_id)
    
    for project_id, role_ids in user_roles.items():
        for role_id in role_ids:
            role_permissions = db_manager.get_role_permissions(role_id)
            for perm in role_permissions:
                resource_type = perm.get('resource_type', 'indicators')
                resource_id = perm.get('resource_id', perm.get('indicator_id', ''))
                action = perm.get('action', perm.get('permission_type', 'read'))
                
                if resource_type and resource_id:
                    perm_str = f"{resource_type}:{resource_id}:{action}"
                    permissions.append(perm_str)
    
    return permissions

def c_timer(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        print(f"函数 {func.__name__} 执行时间: {execution_time:.4f} 秒")
        return result
    return wrapper