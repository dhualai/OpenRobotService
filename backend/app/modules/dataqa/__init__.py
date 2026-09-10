"""dataqa 模块（AI 数据助手）——会话持久化（新建 / 历史 / 删除）。

数据助手问答上下文与摇人对话完全隔离：使用专属表
dataqa_conversations / dataqa_messages，不复用 conversations/messages
与 scene_type 场景区分体系。
"""
from fastapi import APIRouter
from app.modules.dataqa.api.conversation import router as conversation_router
from app.modules.dataqa.api.message import router as message_router

dataqa_router = APIRouter(prefix="/dataqa", tags=["dataqa"])

dataqa_router.include_router(conversation_router)
dataqa_router.include_router(message_router)
