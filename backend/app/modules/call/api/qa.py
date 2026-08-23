"""call AI问答 API（承接 fqa/qa）。

MIGRATION.md 阶段 3：从 `app/modules/fqa/qa/api/qa.py` 搬迁而来，
路由前缀从 `/api/fqa/qa` 迁移到 `/api/call/qa`。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any, AsyncGenerator
from app.core.database import get_async_db as get_db
from app.modules.call.schemas.qa import QuestionRequest, AnswerResponse, StreamingAnswerResponse
from app.modules.call.services.model_service import ModelService
from app.modules.call.services.conversation_service import ConversationService
from app.modules.call.services.message_service import MessageService
from app.modules.call.schemas.conversation import ConversationCreate
from app.modules.call.schemas.message import MessageCreate
from app.modules.call.models.message import MessageRole, MessageType
from app.core.config import settings
import json
from typing import AsyncGenerator

router = APIRouter(prefix="/qa", tags=["call-qa"])


@router.post("/ask", response_model=AnswerResponse, summary="提问接口")
async def ask_question(request: QuestionRequest, db: AsyncSession = Depends(get_db)) -> AnswerResponse:
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
        elif isinstance(response, AsyncGenerator):
            full_answer = ""
            metadata = {}
            async for chunk in response:
                chunk_type = chunk.get("type", "")
                chunk_data = chunk.get("data", "")
                if chunk_type == "metadata":
                    metadata = chunk_data
                elif chunk_type == "content":
                    full_answer += chunk_data
            answer = full_answer if full_answer else "抱歉，AI服务暂不可用，请稍后重试。"
            action = metadata.get("action", "")
            selected_id = metadata.get("selected_id", "")
            current_step = metadata.get("current_step", "")
        else:
            answer = response if response is not None else "抱歉，AI服务暂不可用，请稍后重试。"
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