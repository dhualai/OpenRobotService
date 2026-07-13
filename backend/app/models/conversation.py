"""AI 问询会话与消息 ORM 模型。

原定义于 `app/modules/fqa/qa/models/{conversation,message}.py`，现合并迁入此处作为
唯一定义点（MIGRATION.md 阶段 1）。旧两个路径均改为从本模块再导出。

含 2 张表：conversations / messages
"""
import enum

from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.models.base import Base


class SceneType(str, enum.Enum):
    CHAT = "chat"
    FAQ = "faq"
    SUPPORT = "support"
    CONSULTATION = "consultation"
    OTHER = "other"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    MULTIMODAL = "multimodal"


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False)
    scene_type = Column(Enum(SceneType), nullable=False, default=SceneType.CHAT)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    service_ticket_id = Column(String(255), nullable=False)
    metadata_ = Column(Text, nullable=True, default=None)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    message_type = Column(Enum(MessageType), nullable=False, default=MessageType.TEXT)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    file_urls = Column(Text, nullable=True, default=None)

    parent_message_id = Column(Integer, nullable=True, default=None)
    sequence = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    metadata_ = Column(Text, nullable=True, default=None)

    conversation = relationship("Conversation", back_populates="messages")
