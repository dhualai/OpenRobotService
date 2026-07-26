from typing import Dict, Any, Optional
from datetime import timedelta

from app.core.database import db_manager, get_user_with_roles
from app.core.security import verify_password, create_access_token, create_refresh_token, decode_token, get_password_hash
from app.core.config import settings


class AuthServiceError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


class AuthService:
    @staticmethod
    def login(username: str, password: str) -> Dict[str, Any]:
        user = db_manager.get_user(username)
        if not user:
            raise AuthServiceError(status_code=401, detail="用户名或密码错误")

        if not verify_password(password, user['password_hash']):
            raise AuthServiceError(status_code=401, detail="用户名或密码错误")

        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user['username']}, expires_delta=access_token_expires
        )

        refresh_token = create_refresh_token(data={"sub": user['username']})

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }

    @staticmethod
    def refresh_token(refresh_token: str) -> Dict[str, Any]:
        payload = decode_token(refresh_token)
        if payload is None:
            raise AuthServiceError(status_code=401, detail="无效的刷新令牌")

        if payload.get("type") != "refresh":
            raise AuthServiceError(status_code=401, detail="无效的刷新令牌类型")

        username: str = payload.get("sub")
        if username is None:
            raise AuthServiceError(status_code=401, detail="无效的刷新令牌")

        user = db_manager.get_user(username)
        if not user:
            raise AuthServiceError(status_code=404, detail="用户不存在")

        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user['username']}, expires_delta=access_token_expires
        )

        new_refresh_token = create_refresh_token(data={"sub": user['username']})

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }

    @staticmethod
    def get_user_info(access_token: str) -> Dict[str, Any]:
        payload = decode_token(access_token)
        if payload is None:
            raise AuthServiceError(status_code=401, detail="无效的认证凭据")

        username: str = payload.get("sub")
        if username is None:
            raise AuthServiceError(status_code=401, detail="无效的认证凭据")

        user = get_user_with_roles(username)
        if user is None:
            raise AuthServiceError(status_code=404, detail="用户不存在")

        return {
            "id": user['id'],
            "username": user['username'],
            "name": user.get('name'),
            "status": user.get('status', 'inactive'),
            "permissions": user['permissions'],
            "projectPermissions": user.get('projectPermissions', {}),
            "roles": user['roles']
        }