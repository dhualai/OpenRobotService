"""AI 数据助手（DataQA）会话与消息 ORM 模型。

与摇人对话的 conversations/messages 完全隔离：数据助手问答上下文独立，
故新建 dataqa_conversations / dataqa_messages 两张专属表（结构仿照
conversations/messages，去掉摇人专属的 scene_type / service_ticket_id 字段）。

含 2 张表：dataqa_conversations / dataqa_messages
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.models.base import Base
from app.models.conversation import MessageRole, MessageType


class DataqaConversation(Base):
    __tablename__ = "dataqa_conversations"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    metadata_ = Column(Text, nullable=True, default=None)

    messages = relationship("DataqaMessage", back_populates="conversation", cascade="all, delete-orphan")


class DataqaMessage(Base):
    __tablename__ = "dataqa_messages"

    id = Column(Integer, primary_key=True, index=True)
    message_type = Column(Enum(MessageType), nullable=False, default=MessageType.TEXT)
    conversation_id = Column(Integer, ForeignKey("dataqa_conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    file_urls = Column(Text, nullable=True, default=None)

    parent_message_id = Column(Integer, nullable=True, default=None)
    sequence = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    metadata_ = Column(Text, nullable=True, default=None)

    conversation = relationship("DataqaConversation", back_populates="messages")
