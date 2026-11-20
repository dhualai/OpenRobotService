"""dataqa 消息 API（AI 数据助手专用）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_db as get_db
from app.modules.dataqa.schemas import DataqaMessageCreate, DataqaMessageResponse
from app.modules.dataqa.services import DataqaMessageService

router = APIRouter(prefix="/messages", tags=["dataqa-messages"])


@router.post("", response_model=DataqaMessageResponse, summary="追加一条数据助手消息")
async def create_message(message: DataqaMessageCreate, db: AsyncSession = Depends(get_db)):
    """追加 user/assistant 消息到指定数据助手会话，同时刷新会话 updated_at（列表排序）。"""
    return await DataqaMessageService.create_message(db, message)
