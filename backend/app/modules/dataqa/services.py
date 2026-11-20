"""dataqa 会话/消息服务（AI 数据助手专用表，独立于 call 会话）。"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional, List

from app.models.dataqa import DataqaConversation, DataqaMessage
from app.modules.dataqa.schemas import (
    DataqaConversationCreate,
    DataqaConversationUpdate,
    DataqaMessageCreate,
)
from app.utils.database_utils import DatabaseUtils
from app.utils.data_utils import safe_json_dumps


class DataqaConversationService:
    @staticmethod
    async def create_conversation(db: AsyncSession, conversation: DataqaConversationCreate) -> DataqaConversation:
        conversation_data = conversation.dict()
        # metadata_：前端可能传 JSON 字符串（已序列化）或结构（dict）。仅对非字符串做
        # safe_json_dumps，避免对已序列化字符串二次 dumps 导致双重编码（历史图表/卡片丢失）。
        if conversation_data.get("metadata_") is not None and not isinstance(conversation_data["metadata_"], str):
            conversation_data["metadata_"] = safe_json_dumps(conversation_data["metadata_"])
        return await DatabaseUtils.create_and_commit(db, DataqaConversation, **conversation_data)

    @staticmethod
    async def get_conversation(db: AsyncSession, conversation_id: int) -> Optional[DataqaConversation]:
        # 逻辑删除过滤：软删会话对全部接口不可见（详情 404，重命名/删除拒绝）
        result = await db.execute(
            select(DataqaConversation).filter(
                DataqaConversation.id == conversation_id,
                DataqaConversation.is_deleted.is_(False),
            )
        )
        return result.scalars().first()

    @staticmethod
    async def get_conversations_by_user(db: AsyncSession, user_id: str, skip: int = 0, limit: int = 100) -> List[DataqaConversation]:
        result = await db.execute(
            select(DataqaConversation)
            .filter(
                DataqaConversation.user_id == user_id,
                DataqaConversation.is_deleted.is_(False),
            )
            .order_by(desc(DataqaConversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def update_conversation(db: AsyncSession, conversation_id: int, conversation: DataqaConversationUpdate) -> Optional[DataqaConversation]:
        # 复用 get_conversation 的软删过滤：已删会话不可再修改/重命名
        db_conversation = await DataqaConversationService.get_conversation(db, conversation_id)
        if not db_conversation:
            return None

        update_data = conversation.dict(exclude_unset=True)
        # 同 create_conversation：已序列化字符串直接落库，避免二次 dumps 双重编码
        if update_data.get("metadata_") is not None and not isinstance(update_data["metadata_"], str):
            update_data["metadata_"] = safe_json_dumps(update_data["metadata_"])

        for field, value in update_data.items():
            setattr(db_conversation, field, value)

        return await DatabaseUtils.commit_and_refresh(db, db_conversation)

    @staticmethod
    async def delete_conversation(db: AsyncSession, conversation_id: int) -> bool:
        """逻辑删除：只打标记，不删 dataqa_conversations / dataqa_messages 任何数据。

        与 call 会话同构：数据保留供 AI 统计。原「显式删消息 + db.delete」物理删除已弃用。
        """
        conversation = await DataqaConversationService.get_conversation(db, conversation_id)
        if not conversation:
            return False

        conversation.is_deleted = True
        conversation.deleted_at = datetime.utcnow()
        await db.commit()
        return True


class DataqaMessageService:
    @staticmethod
    async def create_message(db: AsyncSession, message: DataqaMessageCreate) -> DataqaMessage:
        max_sequence = await db.scalar(
            select(func.max(DataqaMessage.sequence))
            .filter(DataqaMessage.conversation_id == message.conversation_id)
        )

        message_data = message.dict()
        message_data["sequence"] = (max_sequence or 0) + 1

        # 追加消息同时刷新所属会话 updated_at：列表按 updated_at 倒序展示，
        # 不刷新会导致在旧会话继续对话时列表排序停滞在创建时刻。
        conv = await db.get(DataqaConversation, message.conversation_id)
        if conv:
            conv.updated_at = func.now()

        # metadata_：前端 appendMessage 传的是 JSON.stringify 后的字符串，若再经
        # safe_json_dumps（json.dumps）会双重编码成带外层引号的字符串，历史恢复
        # JSON.parse 一次只得到字符串，mode/charts/cards 全部丢失。仅对非字符串 dumps。
        if message_data.get("metadata_") is not None and not isinstance(message_data["metadata_"], str):
            message_data["metadata_"] = safe_json_dumps(message_data["metadata_"])

        return await DatabaseUtils.create_and_commit(db, DataqaMessage, **message_data)

    @staticmethod
    async def get_messages_by_conversation(db: AsyncSession, conversation_id: int) -> List[DataqaMessage]:
        result = await db.execute(
            select(DataqaMessage)
            .filter(DataqaMessage.conversation_id == conversation_id)
            .order_by(DataqaMessage.sequence)
        )
        return result.scalars().all()

    @staticmethod
    async def delete_messages_by_conversation(db: AsyncSession, conversation_id: int) -> int:
        result = await db.execute(
            select(DataqaMessage).filter(DataqaMessage.conversation_id == conversation_id)
        )
        messages = result.scalars().all()

        for message in messages:
            await db.delete(message)

        return len(messages)
