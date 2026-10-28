from fastapi import APIRouter, Depends, HTTPException, Request, status
from typing import Dict, Any
from pydantic import BaseModel
from datetime import datetime
from zoneinfo import ZoneInfo
import uuid

from app.core.database import db_manager, get_user_with_roles
from app.core.security import verify_password, create_access_token, create_refresh_token, decode_token, get_password_hash
from app.core.config import settings
from app.core.auth_service import AuthService, AuthServiceError
from app.schemas.token import Token, RefreshToken
from app.schemas.user import User, UserLogin, UserCreate
from app.schemas.response import SuccessResponse
from app.services.working_time_service import WorkingTimeService
from app.services.identity_service import build_upr_id

router = APIRouter()

async def get_current_active_user_from_token(request: Request) -> Dict[str, Any]:
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        token = token[7:]
    elif not token:
        token = request.query_params.get("token", "")
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
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

def require_permission(required_permission: str, project_id: str = None):
    def _match_permission(perm_pattern: str, required_perm: str) -> bool:
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
    
    async def permission_dependency(
        request: Request,
        current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
    ):
        if 'permissions' in current_user and isinstance(current_user['permissions'], (list, set)):
            if "admin" in current_user['permissions']:
                return current_user
            for perm in current_user['permissions']:
                if _match_permission(perm, required_permission):
                    return current_user
        
        if 'roles' in current_user and isinstance(current_user['roles'], dict):
            if "admin" in current_user['roles']:
                return current_user
            for role_permissions in current_user['roles'].values():
                if isinstance(role_permissions, (list, set)) and "admin" in role_permissions:
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

@router.post("/register", response_model=User, summary="用户注册")
def register(user_data: UserCreate):
    if db_manager.get_user(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在"
        )
    
    user_id = user_data.user_id if user_data.user_id else f"user_{uuid.uuid4().hex[:8]}"
    
    hashed_password = get_password_hash(user_data.password)
    
    success = db_manager.add_user(
        user_id=user_id,
        username=user_data.username,
        hashed_password=hashed_password,
        name=user_data.name,
        permissions=["user"]
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="用户注册失败"
        )
    
    default_role_id = "role_e9351224"
    user_project_role_id = build_upr_id(user_id, None, default_role_id)
    
    # 默认角色分配失败不应该阻塞注册流程 —— 用户创建成功是核心目标
    try:
        db_manager.add_user_project_role(
            user_project_role_id,
            user_id,
            None,
            default_role_id
        )
    except Exception as role_err:
        logger.error(f"[auth_routes] ⚠️ 新用户默认角色分配失败（不阻塞注册）: user_id={user_id!r} err={role_err}", exc_info=True)
    
    user = get_user_with_roles(user_data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="无法获取用户信息"
        )
    
    return User(
        id=user['id'],
        username=user['username'],
        name=user.get('name'),
        status=user.get('status', 'inactive'),
        permissions=user['permissions']
    )

@router.post("/login", response_model=Token, summary="用户登录")
def login(user_data: UserLogin):
    try:
        result = AuthService.login(user_data.username, user_data.password)
        return Token(**result)
    except AuthServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

@router.post("/refresh", response_model=Token, summary="刷新访问令牌")
def refresh(refresh_token_data: RefreshToken):
    try:
        result = AuthService.refresh_token(refresh_token_data.refresh_token)
        return Token(**result)
    except AuthServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

@router.get("/me", response_model=User, summary="获取当前用户信息")
async def get_current_user(request: Request):
    token = request.headers.get("Authorization", "")
    token = token[7:]
    try:
        result = AuthService.get_user_info(token)
        return User(**result)
    except AuthServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


def _resolve_user_id(current_user: Dict[str, Any]) -> str:
    """当前用户 users.id：token 载荷形态不一，统一回查一次。"""
    user_id = current_user.get("id") or current_user.get("user_id")
    if user_id:
        return user_id
    username = current_user.get("username")
    user = db_manager.get_user(username) if username else None
    if not user:
        raise HTTPException(status_code=400, detail="无法定位当前用户")
    return user["id"]


class TimezoneUpdate(BaseModel):
    """用户自助更新时区。

    locked=True 表示锁定，锁定后前端自动上报（每次登录检测不一致就覆盖）不再生效，
    适用于外派/长期跨时区办公人员。

    source 用于溯源：浏览器静默上报为 browser，用户在个人中心手工选择为 manual。
    """
    timezone: str
    locked: bool = False
    source: str = "manual"


@router.patch("/me/timezone", summary="更新当前用户时区（可锁定）")
async def update_my_timezone(
    data: TimezoneUpdate,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    tz_name = (data.timezone or "").strip()
    if not tz_name:
        raise HTTPException(status_code=400, detail="时区不能为空")
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise HTTPException(status_code=400, detail=f"非法时区: {tz_name}")

    source = (data.source or "manual").strip().lower()
    if source not in ("browser", "manual", "system"):
        source = "manual"

    user_id = _resolve_user_id(current_user)
    ok = db_manager.update_user(
        user_id,
        timezone=tz_name,
        timezone_locked=1 if data.locked else 0,
        timezone_source=source,
        timezone_updated_at=datetime.utcnow(),
    )
    if not ok:
        raise HTTPException(status_code=500, detail="更新时区失败")

    return {
        "code": 200,
        "message": "时区已更新",
        "timezone": tz_name,
        "locked": bool(data.locked),
    }


@router.get("/me/timezone", summary="获取当前用户时区设置")
async def get_my_timezone(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    user_id = _resolve_user_id(current_user)
    user = db_manager.get_user_by_id(user_id) if user_id else None
    return {
        "timezone": (user or {}).get("timezone"),
        "locked": bool((user or {}).get("timezone_locked")),
        "source": (user or {}).get("timezone_source"),
        "updated_at": (user or {}).get("timezone_updated_at"),
        "system_default": WorkingTimeService.SYSTEM_DEFAULT_TIMEZONE,
        "detected": None,
    }

