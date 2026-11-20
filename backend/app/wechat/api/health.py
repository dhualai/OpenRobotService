from fastapi import APIRouter
from app.wechat.schemas.message import ApiResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["健康检查"])


@router.get("/health", response_model=ApiResponse)
async def health_check():
    try:
        logger.info("健康检查请求")
        return ApiResponse(code=200, message="服务运行正常")
    except Exception as e:
        logger.error(f'健康检查异常: {e}', exc_info=True)
        return ApiResponse(code=500, message="服务器内部错误")