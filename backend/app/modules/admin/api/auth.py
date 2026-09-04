from fastapi import Depends, HTTPException, Request, status
from typing import Dict, Any

from app.core.database import get_user_with_roles
from app.core.security import decode_token


async def get_current_active_user_from_token(request: Request) -> Dict[str, Any]:
    token = request.headers.get("Authorization", "")
    token = token[7:]
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_with_roles(username)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    return user


def _match_permission(perm_pattern: str, required_perm: str) -> bool:
    """匹配权限码，支持通配符 *（段数需一致）。"""
    if perm_pattern == required_perm:
        return True

    pattern_parts = perm_pattern.split(":")
    required_parts = required_perm.split(":")

    if len(pattern_parts) != len(required_parts):
        return False

    for pattern_part, required_part in zip(pattern_parts, required_parts):
        if pattern_part != "*" and pattern_part != required_part:
            return False

    return True


def has_permission_code(current_user: Dict[str, Any], required_permission: str) -> bool:
    """检查用户是否拥有指定权限码（含 admin 直通、角色继承、通配符匹配）。

    与 require_permission 的鉴权逻辑一致，但返回布尔值而非抛出异常，
    适用于同一端点内对部分操作做条件鉴权的场景（如仅当修改他人时才要求权限）。
    """
    if 'permissions' in current_user and isinstance(current_user['permissions'], (list, set)):
        if "admin" in current_user['permissions']:
            return True
        for perm in current_user['permissions']:
            if _match_permission(perm, required_permission):
                return True

    if 'roles' in current_user and isinstance(current_user['roles'], dict):
        if "admin" in current_user['roles']:
            return True
        for role_permissions in current_user['roles'].values():
            if isinstance(role_permissions, (list, set)) and "admin" in role_permissions:
                return True

    return False


def require_permission(required_permission: str, project_id: str = None):
    async def permission_dependency(
        request: Request,
        current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
    ):
        if has_permission_code(current_user, required_permission):
            return current_user

        if project_id and 'projectPermissions' in current_user and isinstance(current_user['projectPermissions'], dict):
            project_perms = current_user['projectPermissions'].get(project_id, {})
            for role_id, role_permissions in project_perms.items():
                if isinstance(role_permissions, (list, set)):
                    for perm in role_permissions:
                        if _match_permission(perm, required_permission):
                            return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足"
        )

    return Depends(permission_dependency)
