"""call 模块（我要摇人）——报障提单 + AI 咨询 + 我的工单跟进，请求方视角。

MIGRATION.md 阶段 3：承接 fqa ask/ask-stream + conversations + 我的工单视角。
认证路由已迁至 core 层：/api/auth/*
"""
from fastapi import APIRouter
from app.modules.call.api.qa import router as qa_router
from app.modules.call.api.conversation import router as conversation_router
from app.modules.call.api.my_tasks import router as my_tasks_router
from app.modules.call.api.message import router as message_router
from app.modules.call.api.attachment import router as attachment_router

call_router = APIRouter(prefix="/call", tags=["call"])

call_router.include_router(qa_router)
call_router.include_router(conversation_router)
call_router.include_router(message_router)
call_router.include_router(my_tasks_router)
call_router.include_router(attachment_router)
