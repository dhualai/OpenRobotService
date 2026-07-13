"""call 会话管理 API（承接 fqa/qa/conversation）。

MIGRATION.md 阶段 3：从 `app/modules/fqa/qa/api/conversation.py` 搬迁而来，
路由前缀从 `/api/fqa/conversations` 迁移到 `/api/call/conversations`。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Any
from app.core.database import get_async_db as get_db
from app.modules.call.schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationWithMessages
)
from app.modules.call.services.conversation_service import ConversationService
from app.modules.call.services.message_service import MessageService
from app.modules.call.models.conversation import SceneType
from app.modules.call.api.auth import get_current_active_user_from_token

router = APIRouter(prefix="/conversations", tags=["call-conversations"])


@router.post("", response_model=ConversationResponse, summary="创建会话")
async def create_conversation(conversation: ConversationCreate, db: AsyncSession = Depends(get_db)):
    return await ConversationService.create_conversation(db, conversation)


@router.get("/{conversation_id}", response_model=ConversationWithMessages, summary="获取会话详情")
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    conversation = await ConversationService.get_conversation(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    messages = await MessageService.get_messages_by_conversation(db, conversation_id)
    return ConversationWithMessages(
        **conversation.__dict__,
        messages=messages
    )


@router.get("", response_model=List[ConversationResponse], summary="获取当前用户的会话列表（管理员可查看全部）")
async def get_conversations(
    scene_type: Optional[SceneType] = Query(None, description="按场景类型过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    user_id = current_user.get("id")
    is_admin = "admin" in current_user.get("permissions", [])
    
    if scene_type:
        return await ConversationService.get_conversations_by_scene_and_user(
            db, scene_type, user_id, is_admin, skip, limit
        )
    else:
        return await ConversationService.get_conversations_with_permission(
            db, user_id, is_admin, skip, limit
        )


@router.put("/{conversation_id}", response_model=ConversationResponse, summary="更新会话")
async def update_conversation(conversation_id: int, conversation: ConversationUpdate, db: AsyncSession = Depends(get_db)):
    updated_conversation = await ConversationService.update_conversation(db, conversation_id, conversation)
    if not updated_conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return updated_conversation


@router.delete("/{conversation_id}", summary="删除会话")
async def delete_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    success = await ConversationService.delete_conversation(db, conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "会话已删除"}