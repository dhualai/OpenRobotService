import httpx
import json
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)


class AIService:
    @staticmethod
    async def call_ai_service(message: dict):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.AI_SERVICE_URL}/api/ai/health",
                    timeout=10.0
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"AI服务调用失败，状态码: {e.response.status_code}, 响应内容: {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"AI服务请求发送失败: {str(e)}")
            raise


try:
    ai_service = AIService()
except Exception as e:
    logger.error(f"创建AI服务实例失败: {str(e)}")
    raise