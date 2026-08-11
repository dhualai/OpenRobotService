from fastapi import APIRouter, Depends, HTTPException, Request, status
from typing import Dict, Any
from pydantic import BaseModel
import uuid

from app.core.database import db_manager, get_user_with_roles
from app.core.security import verify_password, create_access_token, create_refresh_token, decode_token, get_password_hash
from app.core.config import settings
from app.core.auth_service import AuthService, AuthServiceError
from app.schemas.token import Token, RefreshToken
from app.schemas.user import User, UserLogin, UserCreate
from app.schemas.response import SuccessResponse

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
    user_project_role_id = f"upr_{user_id}_global_{default_role_id}"
    
    db_manager.add_user_project_role(
        user_project_role_id,
        user_id,
        None,
        default_role_id
    )
    
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


# ==================== 微信 open_id 绑定（讨论区消息转发到微信用）====================
# 业务账号绑定自己的微信 open_id 后，才能作为"转发到微信"的接收人。
# 微信登录用户（username=wechat_xxx）本身 id 即 open_id，无需绑定。

class BindWechatOpenidRequest(BaseModel):
    open_id: str


@router.post("/me/wechat-openid", summary="绑定当前账号的微信 open_id（转发到微信用）")
async def bind_my_wechat_openid(
    body: BindWechatOpenidRequest,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    from app.models.identity import UserDB

    username = current_user.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="无法识别当前用户")
    open_id = (body.open_id or "").strip()
    if not open_id:
        raise HTTPException(status_code=400, detail="open_id 不能为空")

    db = db_manager.get_db()
    try:
        user = db.query(UserDB).filter(UserDB.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        user.wechat_openid = open_id
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"绑定失败: {str(e)}")
    finally:
        db.close()
    return {"code": 0, "message": "绑定成功", "open_id": open_id}


@router.get("/me/wechat-openid", summary="查询当前账号的微信 open_id 绑定状态")
async def get_my_wechat_openid(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    from app.models.identity import UserDB

    username = current_user.get("username")
    db = db_manager.get_db()
    try:
        user = db.query(UserDB).filter(UserDB.username == username).first()
    finally:
        db.close()
    bound = bool(user and user.wechat_openid)
    # 出于隐私，仅返回是否绑定与脱敏后的 open_id 片段
    masked = None
    if user and user.wechat_openid:
        oid = user.wechat_openid
        masked = (oid[:4] + "***" + oid[-4:]) if len(oid) > 8 else "***"
    return {"bound": bound, "open_id_masked": masked}


@router.delete("/me/wechat-openid", summary="解绑当前账号的微信 open_id")
async def unbind_my_wechat_openid(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    from app.models.identity import UserDB

    username = current_user.get("username")
    db = db_manager.get_db()
    try:
        user = db.query(UserDB).filter(UserDB.username == username).first()
        if user:
            user.wechat_openid = None
            db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"解绑失败: {str(e)}")
    finally:
        db.close()
    return {"code": 0, "message": "已解绑"}