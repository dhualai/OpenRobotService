"""dataqa 会话/消息请求响应模型（AI 数据助手专用）。"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Literal, Union

from app.models.conversation import MessageRole, MessageType

MessageRoleType = Literal["user", "assistant", "system"]


class DataqaConversationCreate(BaseModel):
    title: str = Field(..., description="会话标题", max_length=255)
    user_id: str = Field("", description="用户ID（后端按 token 覆盖）", max_length=255)
    metadata_: Optional[str] = Field(None, description="会话元数据")


class DataqaConversationUpdate(BaseModel):
    title: Optional[str] = Field(None, description="会话标题", max_length=255)
    metadata_: Optional[str] = Field(None, description="会话元数据")


class DataqaMessageCreate(BaseModel):
    conversation_id: int = Field(..., description="所属会话ID")
    content: str = Field(..., description="消息内容")
    role: Union[MessageRoleType, MessageRole] = Field(..., description="消息角色：user、assistant 或 system")
    message_type: MessageType = Field(default=MessageType.TEXT, description="消息类型")
    file_urls: Optional[str] = Field(None, description="JSON格式的文件URL列表")
    parent_message_id: Optional[int] = Field(None, description="父消息ID")
    metadata_: Optional[str] = Field(None, description="消息元数据")


class DataqaMessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: MessageRole
    content: str
    message_type: MessageType
    file_urls: Optional[str] = None
    parent_message_id: Optional[int] = None
    sequence: int
    created_at: datetime
    metadata_: Optional[str] = None

    class Config:
        from_attributes = True


class DataqaConversationResponse(BaseModel):
    id: int
    title: str
    user_id: str
    metadata_: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DataqaConversationWithMessages(DataqaConversationResponse):
    messages: List[DataqaMessageResponse] = []
