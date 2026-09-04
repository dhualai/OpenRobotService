from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional, List, Dict
from app.modules.call.models.message import Message, MessageRole, MessageType
from app.modules.call.schemas.message import MessageCreate, MessageUpdate
from app.utils.database_utils import DatabaseUtils
from app.utils.data_utils import safe_json_loads, safe_json_dumps, generate_content_preview


class MessageService:
    @staticmethod
    async def create_message(db: AsyncSession, message: MessageCreate) -> Message:
        max_sequence = await db.scalar(
            select(func.max(Message.sequence))
            .filter(Message.conversation_id == message.conversation_id)
        )
        
        message_data = message.dict()
        message_data["sequence"] = (max_sequence or 0) + 1
        
        # file_urls：前端可能传 JSON 字符串（已序列化）或结构（list/dict）。
        # 仅对非字符串做 safe_json_dumps，避免对已序列化字符串二次 dumps 导致双重编码。
        if message_data.get("file_urls") is not None and not isinstance(message_data["file_urls"], str):
            message_data["file_urls"] = safe_json_dumps(message_data["file_urls"])
        if message_data.get("metadata_") is not None:
            message_data["metadata_"] = safe_json_dumps(message_data["metadata_"])
        
        return await DatabaseUtils.create_and_commit(db, Message, **message_data)

    @staticmethod
    async def get_message(db: AsyncSession, message_id: int) -> Optional[Message]:
        return await DatabaseUtils.get_by_id(db, Message, message_id)

    @staticmethod
    async def get_messages_by_conversation(db: AsyncSession, conversation_id: int, skip: int = 0, limit: Optional[int] = 100) -> List[Message]:
        # limit=None → 全量（会话详情用：旧会话超 100 条时按 sequence 截尾，
        # 重进会丢最新的收集轮对话与工单卡片）
        query = (
            select(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
            .offset(skip)
        )
        if limit is not None:
            query = query.limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_messages_brief_by_conversation(db: AsyncSession, conversation_id: int) -> List[Dict]:
        result = await db.execute(
            select(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        )
        messages = result.scalars().all()
        
        return [
            {
                "id": msg.id,
                "role": msg.role.value,
                "content_preview": generate_content_preview(msg.content),
                "created_at": msg.created_at
            }
            for msg in messages
        ]

    @staticmethod
    async def get_conversation_history(db: AsyncSession, conversation_id: int, limit: int = 10) -> List[Dict[str, str]]:
        result = await db.execute(
            select(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(desc(Message.sequence))
            .limit(limit)
        )
        messages = result.scalars().all()
        
        return [
            {
                "role": msg.role.value,
                "content": msg.content
            }
            for msg in reversed(messages)
        ]

    @staticmethod
    async def update_message(db: AsyncSession, message_id: int, message: MessageUpdate) -> Optional[Message]:
        db_message = await DatabaseUtils.get_by_id(db, Message, message_id)
        if not db_message:
            return None
        
        update_data = message.dict(exclude_unset=True)
        if update_data.get("file_urls") is not None:
            update_data["file_urls"] = safe_json_dumps(update_data["file_urls"])
        if update_data.get("metadata_") is not None:
            update_data["metadata_"] = safe_json_dumps(update_data["metadata_"])
        
        for field, value in update_data.items():
            setattr(db_message, field, value)
        
        return await DatabaseUtils.commit_and_refresh(db, db_message)

    @staticmethod
    async def delete_message(db: AsyncSession, message_id: int) -> bool:
        message = await DatabaseUtils.get_by_id(db, Message, message_id)
        if not message:
            return False
        
        await db.delete(message)
        await db.commit()
        return True

    @staticmethod
    async def delete_messages_by_conversation(db: AsyncSession, conversation_id: int) -> int:
        result = await db.execute(
            select(Message).filter(Message.conversation_id == conversation_id)
        )
        messages = result.scalars().all()
        
        for message in messages:
            await db.delete(message)
        
        await db.commit()
        return len(messages)