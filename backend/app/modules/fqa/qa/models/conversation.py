from sqlalchemy import Column, Integer, String, DateTime, Text, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
from app.core.database import Base

class SceneType(str, enum.Enum):
    CHAT = "chat"
    FAQ = "faq"
    SUPPORT = "support"
    CONSULTATION = "consultation"
    OTHER = "other"

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