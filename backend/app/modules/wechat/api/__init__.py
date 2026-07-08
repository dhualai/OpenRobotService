from app.modules.wechat.api.wechat import router as wechat_router
from app.modules.wechat.api.message import router as message_router
from app.modules.wechat.api.menu import router as menu_router
from app.modules.wechat.api.health import router as health_router
from app.modules.wechat.api.tag import router as tag_router
from app.modules.wechat.api.debug import router as debug_router

__all__ = ["wechat_router", "message_router", "menu_router", "health_router", "tag_router", "debug_router"]