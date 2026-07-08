from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户提问内容")
    conversation_id: Optional[int] = Field(None, description="可选的会话ID，用于获取上下文")
    system_prompt: Optional[str] = Field("你是一个有用的AI助手。", description="系统提示词")
    include_history: bool = Field(False, description="是否包含历史对话作为上下文")
    action: Optional[str] = Field("", description="上一轮AI响应的action值，用于自定义AI服务")
    selected_id: Optional[str] = Field("", description="上一轮AI响应的selected_id值，用于自定义AI服务")
    current_step: Optional[str] = Field("", description="上一轮AI响应的current_step值，用于自定义AI服务")


class AnswerResponse(BaseModel):
    success: bool = Field(..., description="请求是否成功")
    answer: Optional[str] = Field(None, description="AI生成的回答内容")
    question: str = Field(..., description="用户的原始问题")
    conversation_id: Optional[int] = Field(None, description="相关的会话ID")
    error_message: Optional[str] = Field(None, description="错误信息，如果请求失败")
    action: Optional[str] = Field("", description="AI响应的action值，前端下次请求时需传递")
    selected_id: Optional[str] = Field("", description="AI响应的selected_id值，前端下次请求时需传递")
    current_step: Optional[str] = Field("", description="AI响应的current_step值，前端下次请求时需传递")
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "answer": "这是AI生成的回答内容...",
                "question": "用户的问题是什么？",
                "conversation_id": 123,
                "action": "GENERAL_REPLY",
                "selected_id": "",
                "current_step": ""
            }
        }


class StreamingAnswerResponse(BaseModel):
    event: str = Field("message", description="事件类型")
    data: Dict[str, Optional[str]] = Field(..., description="响应数据")
    done: bool = Field(False, description="是否完成")
    
    class Config:
        json_schema_extra = {
            "example": {
                "event": "message",
                "data": {"content": "回答的一部分..."},
                "done": False
            }
        }