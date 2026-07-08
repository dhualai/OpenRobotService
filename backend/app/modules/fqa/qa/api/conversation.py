from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_async_db as get_db
from app.modules.fqa.qa.schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationWithMessages
)
from app.modules.fqa.qa.services.conversation_service import ConversationService
from app.modules.fqa.qa.services.message_service import MessageService
from app.modules.fqa.qa.models.conversation import SceneType

router = APIRouter(prefix="/conversations")


@router.post("", response_model=ConversationResponse, summary="创建会话")
async def create_conversation(conversation: ConversationCreate, db: AsyncSession = Depends(get_db)):
    """
    创建一个新的会话。
    
    - title: 会话标题
    - user_id: 用户ID
    - service_ticket_id: 服务工单ID
    - scene_type: 场景类型（chat/faq/support/consultation/other）
    - metadata_: 可选的元数据，JSON字符串格式
    """
    return await ConversationService.create_conversation(db, conversation)


@router.get("/{conversation_id}", response_model=ConversationWithMessages, summary="获取会话详情")
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    """
    根据会话ID获取会话详情，包含所有消息。
    """
    conversation = await ConversationService.get_conversation(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    messages = await MessageService.get_messages_by_conversation(db, conversation_id)
    return ConversationWithMessages(
        **conversation.__dict__,
        messages=messages
    )


@router.get("", response_model=List[ConversationResponse], summary="获取会话列表")
async def get_conversations(
    user_id: Optional[str] = Query(None, description="按用户ID过滤"),
    scene_type: Optional[SceneType] = Query(None, description="按场景类型过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    """
    获取会话列表。
    
    - user_id: 可选，按用户ID过滤
    - scene_type: 可选，按场景类型过滤
    - skip: 跳过的记录数
    - limit: 返回的记录数
    """
    if user_id:
        return await ConversationService.get_conversations_by_user(db, user_id, skip, limit)
    elif scene_type:
        return await ConversationService.get_conversations_by_scene(db, scene_type, skip, limit)
    else:
        raise HTTPException(status_code=400, detail="必须提供 user_id 或 scene_type 参数")


@router.put("/{conversation_id}", response_model=ConversationResponse, summary="更新会话")
async def update_conversation(conversation_id: int, conversation: ConversationUpdate, db: AsyncSession = Depends(get_db)):
    """
    更新会话信息。
    
    - title: 可选，新的会话标题
    - scene_type: 可选，新的场景类型
    - metadata_: 可选，新的元数据
    """
    updated_conversation = await ConversationService.update_conversation(db, conversation_id, conversation)
    if not updated_conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return updated_conversation


@router.delete("/{conversation_id}", summary="删除会话")
async def delete_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    """
    删除会话及其所有消息。
    """
    success = await ConversationService.delete_conversation(db, conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "会话已删除"}