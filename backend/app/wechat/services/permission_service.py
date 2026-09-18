from typing import Dict, Any
from app.services.user_service import user_service


class PermissionService:
    @staticmethod
    async def get_user_list(request, token: str) -> Dict[str, Any]:
        try:
            # 与项目其他列表接口约定一致：默认 limit=999999999 取全量，
            # 避免默认 limit=100 截断后 send_notification_core 兜底拿不到真实 openid
            return user_service.get_user_list(limit=999999999)
        except Exception as e:
            raise Exception(f"获取用户列表失败: {str(e)}")