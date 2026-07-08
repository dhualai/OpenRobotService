from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_async_db as get_db
from app.modules.fqa.qa.schemas.message import (
    MessageCreate,
    MessageResponse,
    MessageBrief,
    MessageUpdate
)
from app.modules.fqa.qa.services.message_service import MessageService

router = APIRouter(prefix="/messages")


@router.post("", response_model=MessageResponse, summary="创建消息")
async def create_message(message: MessageCreate, db: AsyncSession = Depends(get_db)):
    """
    创建一条新消息。
    
    - conversation_id: 所属会话ID
    - content: 消息内容
    - role: 消息角色（user/assistant/system）
    - message_type: 消息类型（text/image/file/audio/multimodal）
    - file_urls: 可选，文件URL列表（JSON字符串）
    - parent_message_id: 可选，父消息ID
    - metadata_: 可选，元数据（JSON字符串）
    """
    return await MessageService.create_message(db, message)


@router.get("/{message_id}", response_model=MessageResponse, summary="获取消息")
async def get_message(message_id: int, db: AsyncSession = Depends(get_db)):
    """
    根据消息ID获取消息详情。
    """
    message = await MessageService.get_message(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="消息不存在")
    return message


@router.get("", response_model=List[MessageResponse], summary="获取会话消息列表")
async def get_messages(
    conversation_id: int = Query(..., description="会话ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    """
    获取指定会话的消息列表。
    
    - conversation_id: 必填，会话ID
    - skip: 跳过的记录数
    - limit: 返回的记录数
    """
    return await MessageService.get_messages_by_conversation(db, conversation_id, skip, limit)


@router.get("/{conversation_id}/brief", response_model=List[MessageBrief], summary="获取会话消息概览")
async def get_messages_brief(conversation_id: int, db: AsyncSession = Depends(get_db)):
    """
    获取指定会话的消息概览，只包含必要信息。
    """
    return await MessageService.get_messages_brief_by_conversation(db, conversation_id)


@router.put("/{message_id}", response_model=MessageResponse, summary="更新消息")
async def update_message(message_id: int, message: MessageUpdate, db: AsyncSession = Depends(get_db)):
    """
    更新消息内容。
    
    - content: 可选，新的消息内容
    - role: 可选，新的消息角色
    - message_type: 可选，新的消息类型
    - file_urls: 可选，新的文件URL列表
    - parent_message_id: 可选，新的父消息ID
    - metadata_: 可选，新的元数据
    """
    updated_message = await MessageService.update_message(db, message_id, message)
    if not updated_message:
        raise HTTPException(status_code=404, detail="消息不存在")
    return updated_message


@router.delete("/{message_id}", summary="删除消息")
async def delete_message(message_id: int, db: AsyncSession = Depends(get_db)):
    """
    删除单条消息。
    """
    success = await MessageService.delete_message(db, message_id)
    if not success:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"message": "消息已删除"}