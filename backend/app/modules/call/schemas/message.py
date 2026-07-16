from pydantic import BaseModel, Field, field_serializer
from datetime import datetime
from typing import Optional, Literal, List, Union
from app.modules.call.models.message import MessageType, MessageRole

MessageRoleType = Literal["user", "assistant", "system"]

class MessageBase(BaseModel):
    content: str = Field(..., description="消息内容")
    role: Union[MessageRoleType, MessageRole] = Field(..., description="消息角色：user、assistant 或 system")
    message_type: MessageType = Field(default=MessageType.TEXT, description="消息类型")
    file_urls: Optional[str] = Field(None, description="JSON格式的文件URL列表")
    parent_message_id: Optional[int] = Field(None, description="父消息ID")
    metadata_: Optional[str] = Field(None, description="消息元数据")
    
    @field_serializer('role')
    def serialize_role(self, role: Union[MessageRoleType, MessageRole]) -> str:
        if isinstance(role, MessageRole):
            return role.value
        return role
    
    @field_serializer('message_type')
    def serialize_message_type(self, message_type: MessageType) -> str:
        if isinstance(message_type, MessageType):
            return message_type.value
        return message_type

class MessageCreate(MessageBase):
    conversation_id: int = Field(..., description="所属会话ID")

class MessageResponse(MessageBase):
    id: int
    conversation_id: int
    sequence: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class MessageBrief(BaseModel):
    id: int
    role: MessageRoleType
    content_preview: str = Field(..., description="消息内容预览")
    created_at: datetime
    
    class Config:
        from_attributes = True

class MessageUpdate(BaseModel):
    content: Optional[str] = None
    role: Optional[MessageRoleType] = None
    message_type: Optional[MessageType] = None
    file_urls: Optional[List[str]] = None
    parent_message_id: Optional[int] = None
    metadata_: Optional[dict] = None
    
    class Config:
        from_attributes = True