from typing import Dict, Optional, Union
from pydantic import BaseModel, field_validator


class SendMessageRequest(BaseModel):
    open_id: str
    content: str
    url: Optional[str] = None


class SendLinkMessageRequest(BaseModel):
    open_id: str
    title: str
    description: str
    url: str


class BroadcastMessageRequest(BaseModel):
    content: str


class TemplateMessageRequest(BaseModel):
    open_id: str
    template_id: str
    data: Dict[str, Dict[str, str]]
    miniprogram: Optional[Dict[str, str]] = None


class ApiResponse(BaseModel):
    code: int
    message: str


class LinkMessageRequest(BaseModel):
    content: str
    url: str


class TemplateMessageData(BaseModel):
    id: str
    data: Dict[str, Dict]
    url: str


class AtRequest(BaseModel):
    user_names: list[str]
    is_all: bool


class SendNotificationRequest(BaseModel):
    msg_type: str
    message_id: str
    link: Optional[LinkMessageRequest] = None
    template: Optional[TemplateMessageData] = None
    at: AtRequest

    @field_validator('link')
    @classmethod
    def validate_link(cls, v, info):
        msg_type = info.data.get('msg_type')
        if msg_type == 'link' and v is None:
            raise ValueError('msg_type为link时，link字段不能为空')
        return v

    @field_validator('template')
    @classmethod
    def validate_template(cls, v, info):
        msg_type = info.data.get('msg_type')
        if msg_type == 'template' and v is None:
            raise ValueError('msg_type为template时，template字段不能为空')
        return v


class RecipientResponse(BaseModel):
    user_name: str
    name: str = None
    status: str
    platform: str
    error_code: str = None
    error_message: str = None


class NotificationResponse(BaseModel):
    code: int
    message: str
    message_id: str
    data: dict
    timestamp: str


class WechatMessage(BaseModel):
    to_user_name: str
    from_user_name: str
    create_time: int
    msg_type: str


class WechatTextMessage(WechatMessage):
    content: str
    msg_id: str


class WechatEventMessage(WechatMessage):
    event: str
    event_key: str = None