import logging
from fastapi import APIRouter
from app.wechat.api import wechat, message, menu, health, tag, debug, notify

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/wechat")

logger.info("注册微信相关路由")
api_router.include_router(wechat.router)
logger.info("注册消息相关路由")
api_router.include_router(message.router)
logger.info("注册菜单相关路由")
api_router.include_router(menu.router)
logger.info("注册健康检查路由")
api_router.include_router(health.router)
logger.info("注册标签相关路由")
api_router.include_router(tag.router)
logger.info("注册调试工具路由")
api_router.include_router(debug.router)
logger.info("注册通知路由")
api_router.include_router(notify.router)
logger.info("所有路由注册完成")