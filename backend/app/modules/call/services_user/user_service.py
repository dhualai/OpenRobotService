from typing import Any, Dict, Optional

from app.modules.aas.services.auth_service import AuthService, AuthServiceError


class AASClientError(Exception):
    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


class UserService:
    @classmethod
    async def login(cls, username: str, password: str) -> Dict[str, Any]:
        try:
            return AuthService.login(username, password)
        except AuthServiceError as e:
            raise AASClientError(status_code=e.status_code, detail=e.detail) from e

    @classmethod
    async def refresh_token(cls, refresh_token: str) -> Dict[str, Any]:
        try:
            return AuthService.refresh_token(refresh_token)
        except AuthServiceError as e:
            raise AASClientError(status_code=e.status_code, detail=e.detail) from e

    @classmethod
    async def get_user_info(cls, access_token: str) -> dict:
        try:
            return AuthService.get_user_info(access_token)
        except AuthServiceError as e:
            raise AASClientError(status_code=e.status_code, detail=e.detail) from e