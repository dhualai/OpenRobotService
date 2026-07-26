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
        return await DatabaseUtils.get_by_id(db, Conversation, conversation_id)

    @staticmethod
    async def get_conversations_by_user(db: AsyncSession, user_id: str, skip: int = 0, limit: int = 100) -> List[Conversation]:
        result = await db.execute(
            select(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(desc(Conversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_conversations_by_scene(db: AsyncSession, scene_type: SceneType, skip: int = 0, limit: int = 100) -> List[Conversation]:
        result = await db.execute(
            select(Conversation)
            .filter(Conversation.scene_type == scene_type)
            .order_by(desc(Conversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_conversations_with_permission(db: AsyncSession, user_id: str, is_admin: bool, skip: int = 0, limit: int = 100) -> List[Conversation]:
        query = select(Conversation).order_by(desc(Conversation.updated_at)).offset(skip).limit(limit)
        if not is_admin:
            query = query.filter(Conversation.user_id == user_id)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_conversations_by_scene_and_user(db: AsyncSession, scene_type: SceneType, user_id: str, is_admin: bool, skip: int = 0, limit: int = 100) -> List[Conversation]:
        query = select(Conversation).filter(Conversation.scene_type == scene_type).order_by(desc(Conversation.updated_at)).offset(skip).limit(limit)
        if not is_admin:
            query = query.filter(Conversation.user_id == user_id)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update_conversation(db: AsyncSession, conversation_id: int, conversation: ConversationUpdate) -> Optional[Conversation]:
        db_conversation = await DatabaseUtils.get_by_id(db, Conversation, conversation_id)
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
        conversation = await DatabaseUtils.get_by_id(db, Conversation, conversation_id)
        if not conversation:
            return False
        
        await db.delete(conversation)
        await db.commit()
        return True