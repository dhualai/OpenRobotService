"""dataqa 会话管理 API（AI 数据助手专用，独立于 call 会话）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from app.core.database import get_async_db as get_db
from app.core.auth_routes import get_current_active_user_from_token
from app.modules.dataqa.schemas import (
    DataqaConversationCreate,
    DataqaConversationUpdate,
    DataqaConversationResponse,
    DataqaConversationWithMessages,
)
from app.modules.dataqa.services import DataqaConversationService, DataqaMessageService

router = APIRouter(prefix="/conversations", tags=["dataqa-conversations"])


@router.post("", response_model=DataqaConversationResponse, summary="创建数据助手会话")
async def create_conversation(
    conversation: DataqaConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    # user_id 以 token 为准：前端只持有 username，无法可靠传递与列表查询一致的 user_id
    if current_user.get("id"):
        conversation.user_id = current_user["id"]
    return await DataqaConversationService.create_conversation(db, conversation)


@router.get("", response_model=List[DataqaConversationResponse], summary="当前用户的数据助手会话列表")
async def get_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    # 数据助手是个人私有问答：不提供 admin 全局视角，统一按 user_id 隔离
    return await DataqaConversationService.get_conversations_by_user(
        db, current_user.get("id", ""), skip, limit
    )


@router.get("/{conversation_id}", response_model=DataqaConversationWithMessages, summary="获取会话详情（含消息）")
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    conversation = await DataqaConversationService.get_conversation(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = await DataqaMessageService.get_messages_by_conversation(db, conversation_id)
    return DataqaConversationWithMessages(
        **conversation.__dict__,
        messages=messages,
    )


@router.put("/{conversation_id}", response_model=DataqaConversationResponse, summary="更新会话")
async def update_conversation(
    conversation_id: int,
    conversation: DataqaConversationUpdate,
    db: AsyncSession = Depends(get_db),
):
    updated_conversation = await DataqaConversationService.update_conversation(db, conversation_id, conversation)
    if not updated_conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return updated_conversation


@router.delete("/{conversation_id}", summary="删除会话（连同消息）")
async def delete_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    success = await DataqaConversationService.delete_conversation(db, conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "会话已删除"}
