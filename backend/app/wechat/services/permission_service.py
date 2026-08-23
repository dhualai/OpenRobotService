from typing import Dict, Any
from app.services.user_service import user_service


class PermissionService:
    @staticmethod
    async def get_user_list(request, token: str) -> Dict[str, Any]:
        try:
            return user_service.get_user_list()
        except Exception as e:
            raise Exception(f"获取用户列表失败: {str(e)}")