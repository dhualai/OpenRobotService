from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
from app.modules.call.schemas.message import MessageResponse
from app.modules.call.models.conversation import SceneType

class ConversationBase(BaseModel):
    title: str = Field(..., description="会话标题", max_length=255)
    user_id: str = Field(..., description="用户ID", max_length=255)
    service_ticket_id: str = Field(..., description="服务工单ID", max_length=255)
    scene_type: SceneType = Field(default=SceneType.CHAT, description="场景类型")
    metadata_: Optional[str] = Field(None, description="会话元数据")

class ConversationCreate(ConversationBase):
    pass

class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(None, description="会话标题", max_length=255)
    scene_type: Optional[SceneType] = Field(None, description="场景类型")
    metadata_: Optional[str] = Field(None, description="会话元数据")

class ConversationResponse(ConversationBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class ConversationWithMessages(ConversationResponse):
    messages: List[MessageResponse] = []