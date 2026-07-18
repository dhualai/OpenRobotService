import logging
from fastapi import APIRouter
from app.wechat.api import wechat, message, menu, health, tag, debug, notify

logger = logging.getLogger(__name__)

# 注意：/wechat 前缀放在每次 include_router 调用上，而不是 api_router 上。
# 因为 wechat.router 的微信验证/消息回调使用了空路径 ("")，
# 若 api_router 带 prefix 且 include 时 prefix 也为空，FastAPI 会抛出
# "Prefix and path cannot be both empty"。把前缀下放到 include 处可绕过该校验，
# 同时保证验证地址仍为 /api/wechat（无尾斜杠，避免微信验证因 307 重定向失败）。
api_router = APIRouter()

logger.info("注册微信相关路由")
api_router.include_router(wechat.router, prefix="/wechat")
logger.info("注册消息相关路由")
api_router.include_router(message.router, prefix="/wechat")
logger.info("注册菜单相关路由")
api_router.include_router(menu.router, prefix="/wechat")
logger.info("注册健康检查路由")
api_router.include_router(health.router, prefix="/wechat")
logger.info("注册标签相关路由")
api_router.include_router(tag.router, prefix="/wechat")
logger.info("注册调试工具路由")
api_router.include_router(debug.router, prefix="/wechat")
logger.info("注册通知路由")
api_router.include_router(notify.router, prefix="/wechat")
logger.info("所有路由注册完成")