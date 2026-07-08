from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any, AsyncGenerator
from app.core.database import get_async_db as get_db
from app.modules.fqa.qa.schemas.qa import QuestionRequest, AnswerResponse, StreamingAnswerResponse
from app.modules.fqa.qa.services.model_service import ModelService
from app.modules.fqa.qa.services.conversation_service import ConversationService
from app.modules.fqa.qa.services.message_service import MessageService
from app.modules.fqa.qa.schemas.conversation import ConversationCreate
from app.modules.fqa.qa.schemas.message import MessageCreate
from app.modules.fqa.qa.models.message import MessageRole, MessageType
from app.core.config import settings
import json

router = APIRouter()


@router.post("/ask", response_model=AnswerResponse, summary="提问接口")
async def ask_question(request: QuestionRequest, db: AsyncSession = Depends(get_db)) -> AnswerResponse:
    """
    接受用户提问，返回AI生成的回答。
    
    - 如果提供了 conversation_id，则会获取历史对话作为上下文。
    - 如果没有提供 conversation_id，则会创建一个新的会话。
    - 支持通过 action/selected_id/current_step 参数与自定义AI服务交互。
    """
    try:
        conversation_id = request.conversation_id
        conversation = None
        
        if conversation_id:
            conversation = await ConversationService.get_conversation(db, conversation_id)
            if not conversation:
                conversation_id = None
        
        conversation_history = []
        if conversation and request.include_history:
            conversation_history = await MessageService.get_conversation_history(db, conversation_id)
        
        response = await ModelService.generate_answer(
            request.question,
            request.system_prompt,
            conversation_history,
            request.action,
            request.selected_id,
            request.current_step
        )
        
        if isinstance(response, dict):
            answer = response.get("answer", "")
            action = response.get("action", "")
            selected_id = response.get("selected_id", "")
            current_step = response.get("current_step", "")
        else:
            answer = response
            action = ""
            selected_id = ""
            current_step = ""
        
        if not conversation:
            conversation = await ConversationService.create_conversation(
                db,
                ConversationCreate(
                    title=request.question[:50] if len(request.question) > 50 else request.question,
                    user_id="default_user",
                    service_ticket_id="",
                    scene_type="chat"
                )
            )
            conversation_id = conversation.id
        
        await MessageService.create_message(
            db,
            MessageCreate(
                conversation_id=conversation_id,
                role=MessageRole.USER.value,
                content=request.question,
                message_type=MessageType.TEXT
            )
        )
        
        await MessageService.create_message(
            db,
            MessageCreate(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT.value,
                content=answer,
                message_type=MessageType.TEXT,
                metadata_=json.dumps({
                    "action": action,
                    "selected_id": selected_id,
                    "current_step": current_step
                }) if any([action, selected_id, current_step]) else None
            )
        )
        
        return AnswerResponse(
            success=True,
            answer=answer,
            question=request.question,
            conversation_id=conversation_id,
            action=action,
            selected_id=selected_id,
            current_step=current_step
        )
    
    except Exception as e:
        return AnswerResponse(
            success=False,
            answer=None,
            question=request.question,
            conversation_id=conversation_id,
            error_message=str(e)
        )


@router.post("/ask/stream", summary="流式提问接口", response_model=None)
async def ask_question_stream(request: QuestionRequest, db: AsyncSession = Depends(get_db)) -> AsyncGenerator[StreamingAnswerResponse, None]:
    """
    接受用户提问，以流式方式返回AI生成的回答。
    
    - 如果提供了 conversation_id，则会获取历史对话作为上下文。
    - 如果没有提供 conversation_id，则会创建一个新的会话。
    """
    try:
        conversation_id = request.conversation_id
        conversation = None
        
        if conversation_id:
            conversation = await ConversationService.get_conversation(db, conversation_id)
            if not conversation:
                conversation_id = None
        
        conversation_history = []
        if conversation and request.include_history:
            conversation_history = await MessageService.get_conversation_history(db, conversation_id)
        
        response = await ModelService.generate_answer(
            request.question,
            request.system_prompt,
            conversation_history,
            request.action,
            request.selected_id,
            request.current_step
        )
        
        if not conversation:
            conversation = await ConversationService.create_conversation(
                db,
                ConversationCreate(
                    title=request.question[:50] if len(request.question) > 50 else request.question,
                    user_id="default_user",
                    service_ticket_id="",
                    scene_type="chat"
                )
            )
            conversation_id = conversation.id
        
        await MessageService.create_message(
            db,
            MessageCreate(
                conversation_id=conversation_id,
                role=MessageRole.USER.value,
                content=request.question,
                message_type=MessageType.TEXT
            )
        )
        
        full_answer = ""
        metadata = {}
        
        async for chunk in response:
            chunk_type = chunk.get("type", "")
            chunk_data = chunk.get("data", "")
            
            if chunk_type == "metadata":
                metadata = chunk_data
            elif chunk_type == "content":
                full_answer += chunk_data
                yield StreamingAnswerResponse(
                    event="message",
                    data={"content": chunk_data},
                    done=False
                )
        
        await MessageService.create_message(
            db,
            MessageCreate(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT.value,
                content=full_answer,
                message_type=MessageType.TEXT,
                metadata_=json.dumps(metadata) if metadata else None
            )
        )
        
        yield StreamingAnswerResponse(
            event="message",
            data={
                "content": "",
                "action": metadata.get("action", ""),
                "selected_id": metadata.get("selected_id", ""),
                "current_step": metadata.get("current_step", "")
            },
            done=True
        )
    
    except Exception as e:
        yield StreamingAnswerResponse(
            event="error",
            data={"content": f"错误: {str(e)}"},
            done=True
        )