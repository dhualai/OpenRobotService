from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional, List
from app.modules.call.models.conversation import Conversation, SceneType
from app.modules.call.schemas.conversation import ConversationCreate, ConversationUpdate
from app.utils.database_utils import DatabaseUtils
from app.utils.data_utils import safe_json_loads, safe_json_dumps


class ConversationService:
    @staticmethod
    async def create_conversation(db: AsyncSession, conversation: ConversationCreate) -> Conversation:
        conversation_data = conversation.dict()
        if conversation_data.get("metadata_") is not None:
            conversation_data["metadata_"] = safe_json_dumps(conversation_data["metadata_"])
        return await DatabaseUtils.create_and_commit(db, Conversation, **conversation_data)

    @staticmethod
    async def get_conversation(db: AsyncSession, conversation_id: int) -> Optional[Conversation]:
        # 逻辑删除过滤：软删会话对全部接口不可见（详情 404，重命名/删除拒绝）
        result = await db.execute(
            select(Conversation).filter(
                Conversation.id == conversation_id,
                Conversation.is_deleted.is_(False),
            )
        )
        return result.scalars().first()

    @staticmethod
    async def get_conversations_by_user(db: AsyncSession, user_id: str, skip: int = 0, limit: int = 100) -> List[Conversation]:
        result = await db.execute(
            select(Conversation)
            .filter(
                Conversation.user_id == user_id,
                Conversation.is_deleted.is_(False),
            )
            .order_by(desc(Conversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_conversations_by_scene(db: AsyncSession, scene_type: SceneType, skip: int = 0, limit: int = 100) -> List[Conversation]:
        result = await db.execute(
            select(Conversation)
            .filter(
                Conversation.scene_type == scene_type,
                Conversation.is_deleted.is_(False),
            )
            .order_by(desc(Conversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_conversations_with_permission(db: AsyncSession, user_id: str, is_admin: bool, skip: int = 0, limit: int = 100) -> List[Conversation]:
        query = (
            select(Conversation)
            .filter(Conversation.is_deleted.is_(False))
            .order_by(desc(Conversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        if not is_admin:
            query = query.filter(Conversation.user_id == user_id)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_conversations_by_scene_and_user(db: AsyncSession, scene_type: SceneType, user_id: str, is_admin: bool, skip: int = 0, limit: int = 100) -> List[Conversation]:
        query = (
            select(Conversation)
            .filter(
                Conversation.scene_type == scene_type,
                Conversation.is_deleted.is_(False),
            )
            .order_by(desc(Conversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        if not is_admin:
            query = query.filter(Conversation.user_id == user_id)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update_conversation(db: AsyncSession, conversation_id: int, conversation: ConversationUpdate) -> Optional[Conversation]:
        # 复用 get_conversation 的软删过滤：已删会话不可再修改/重命名
        db_conversation = await ConversationService.get_conversation(db, conversation_id)
        if not db_conversation:
            return None
        
        update_data = conversation.dict(exclude_unset=True)
        if update_data.get("metadata_") is not None:
            update_data["metadata_"] = safe_json_dumps(update_data["metadata_"])
        
        for field, value in update_data.items():
            setattr(db_conversation, field, value)
        
        return await DatabaseUtils.commit_and_refresh(db, db_conversation)

    @staticmethod
    async def delete_conversation(db: AsyncSession, conversation_id: int) -> bool:
        """逻辑删除：只打标记，不删 conversations / messages 任何数据。

        AI 侧数据分析（直达 / 派单准确率）需要会话原文全量保留，故此处严禁
        db.delete（ORM cascade 会连带物理删除 messages）。重复删除幂等返回 False。
        """
        conversation = await ConversationService.get_conversation(db, conversation_id)
        if not conversation:
            return False
        
        conversation.is_deleted = True
        conversation.deleted_at = datetime.utcnow()
        await db.commit()
        return True