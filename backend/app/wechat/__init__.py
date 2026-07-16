"""wechat 模块（微信外壳）——菜单/消息/标签/OAuth/模板通知。

MIGRATION.md 阶段 3：从 `app/modules/wechat/` 上移到顶层 `app/wechat/`，
合入 das/notify 通知功能。

包含：菜单管理、OAuth鉴权、消息回调、模板通知、用户标签、健康检查、调试工具、通知推送。
"""
from fastapi import APIRouter
from app.wechat.api.routes import api_router as wechat_api_router

__all__ = ["wechat_api_router"]
