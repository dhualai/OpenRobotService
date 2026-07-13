"""
统一 API 路由
  /api/ai/qa/*    诊断 Agent
  /api/ai/chat/*  纯 LLM 对话
  /api/ai/memory/* 会话记忆
"""
import json
import time
import os
from pathlib import Path
from typing import List
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai.core import get_llm_client, get_memory_manager
from app.ai.config import get_ai_config
from app.ai.agents.AiDiagnosisPlatform.pipeline import (
    get_diagnosis_platform, AiDiagnosisPlatform, DiagnosisRequest,
)

# ============================================================
# 诊断 Agent (prefix /api/ai/qa)
# ============================================================
qa_router = APIRouter(prefix="/api/ai/qa", tags=["AI诊断"])


class QAAskRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")
    query: str = Field(..., min_length=1, max_length=500, description="用户输入")
    skip_retrieval: bool = Field(default=False, description="测试用：跳过 KB 检索")


class QASubmitRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")


class TicketAckRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    dispatch_id: str = Field(default="", description="派单系统内部工单 ID")
    status: str = Field(default="dispatched", description="派单状态")


async def get_pipeline() -> AiDiagnosisPlatform:
    return await get_diagnosis_platform()


@qa_router.post("/ask", summary="统一问答（含诊断追问与提单）")
async def ask_question(
    request: QAAskRequest,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    qa_request = DiagnosisRequest(session_id=request.session_id, query=request.query,
                           skip_retrieval=request.skip_retrieval)
    try:
        result = await pipeline.run_with_timeout(qa_request, timeout=30.0)
    except Exception as e:
        return {"code": 1, "message": f"系统错误: {str(e)}"}
    if "code" not in result:
        result["code"] = 0
    return result


@qa_router.post("/ask/stream", summary="流式问答（SSE）")
async def ask_question_stream(
    request: QAAskRequest,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
):
    async def sse():
        t0 = time.perf_counter()
        qa_request = DiagnosisRequest(session_id=request.session_id, query=request.query,
                           skip_retrieval=request.skip_retrieval)
        first = False
        try:
            async for event in pipeline.run_stream(qa_request):
                ev_type = event["event"]
                if ev_type == "token":
                    if not first:
                        first = True
                        yield f"event: first_token\ndata: {json.dumps({'ms': round((time.perf_counter() - t0) * 1000)}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'token': event['data']}, ensure_ascii=False)}\n\n"
                elif ev_type == "result":
                    yield f"event: result\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                else:
                    yield f"event: status\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'total_ms': round((time.perf_counter() - t0) * 1000)})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
    return StreamingResponse(sse(), media_type="text/event-stream")


@qa_router.post("/submit", summary="生成工单并存库")
async def submit_ticket(
    request: QASubmitRequest,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    result = await pipeline.submit(session_id=request.session_id)
    if "code" not in result:
        result["code"] = 0
    return result


@qa_router.get("/ticket", summary="获取工单数据")
async def get_ticket(session_id: str = Query(..., description="会话 ID")):
    try:
        mgr = await get_memory_manager()
        memory = await mgr.get_memory(session_id)
        agent_state = memory.metadata.get("agent_state", {})
        if agent_state.get("phase") not in ("escalated", "resolved"):
            return {"code": 1, "message": "该会话尚未生成工单"}
        pipeline = await get_pipeline()
        ticket = await pipeline.get_ticket(session_id)
        return {"code": 0, "data": ticket}
    except Exception as e:
        return {"code": 1, "message": str(e)}


@qa_router.post("/ticket/ack", summary="派单确认回执")
async def ack_ticket(request: TicketAckRequest):
    try:
        mgr = await get_memory_manager()
        await mgr.remove_pending_ticket(request.session_id)
        memory = await mgr.get_memory(request.session_id)
        state = memory.metadata.get("agent_state", {})
        state["dispatch"] = {"dispatch_id": request.dispatch_id, "status": request.status}
        memory.metadata["agent_state"] = state
        await mgr.save_memory(memory)
        return {"code": 0, "data": {"session_id": request.session_id, "message": "已确认"}}
    except Exception as e:
        return {"code": 1, "message": str(e)}


@qa_router.post("/upload", summary="上传附件")
async def upload_files(
    session_id: str = Form(..., description="会话 ID"),
    files: List[UploadFile] = File(..., description="附件文件"),
):
    config = get_ai_config()
    upload_root = Path(config.upload_dir)
    session_dir = upload_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        file_path = session_dir / f.filename
        content = await f.read()
        file_path.write_bytes(content)
        saved.append({"filename": f.filename, "size": len(content), "path": str(file_path)})
    filenames = "、".join(s["filename"] for s in saved)
    mgr = await get_memory_manager()
    await mgr.add_turn(session_id, "user", f"[上传了附件] {filenames}")
    await mgr.add_turn(session_id, "assistant", f"已收到 {len(saved)} 个文件，已附到本次会话中。")
    try:
        memory = await mgr.get_memory(session_id)
        state = memory.metadata.get("agent_state", {})
        existing = state.get("attachments", [])
        state["attachments"] = existing + saved
        memory.metadata["agent_state"] = state
        await mgr.save_memory(memory)
    except Exception:
        pass
    return {"code": 0, "data": {"saved": len(saved), "files": saved}}


@qa_router.get("/health", summary="健康检查")
async def qa_health() -> dict:
    from app.ai.config import validate_ai_config
    try:
        results = await validate_ai_config()
        all_ok = all(r["status"] == "ok" for r in results.values())
        return {"code": 0 if all_ok else 1, "data": {"status": "healthy" if all_ok else "degraded", "services": results}}
    except Exception as e:
        return {"code": 1, "data": {"status": "unhealthy", "error": str(e)}}


# ============================================================
# 纯 LLM 对话 (prefix /api/ai/chat)
# ============================================================
chat_router = APIRouter(prefix="/api/ai/chat", tags=["AI对话"])


class ChatRequest(BaseModel):
    session_id: str = Field(default="default")
    query: str = Field(..., min_length=1, max_length=2000)
    max_tokens: int = Field(default=2000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    system_prompt: str = Field(default="", max_length=2000, description="可选系统提示词")


async def _save_memory(session_id: str, query: str, answer: str):
    try:
        mgr = await get_memory_manager()
        await mgr.add_turn(session_id, "user", query)
        await mgr.add_turn(session_id, "assistant", answer)
    except Exception:
        pass


async def _build_prompt(session_id: str, query: str) -> str:
    try:
        mgr = await get_memory_manager()
        history = await mgr.get_context(session_id)
        if not history:
            return query
        lines = ["以下是历史对话："]
        for h in history:
            role = "用户" if h["role"] == "user" else "助手"
            lines.append(f"{role}：{h['content']}")
        lines.append(f"用户：{query}")
        return "\n".join(lines)
    except Exception:
        return query


@chat_router.post("", summary="LLM 对话（非流式）")
async def chat(request: ChatRequest) -> dict:
    llm = await get_llm_client()
    try:
        prompt = await _build_prompt(request.session_id, request.query)
        t0 = time.perf_counter()
        answer = await llm.complete(
            prompt=prompt,
            system_prompt=request.system_prompt or None,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
        total_ms = round((time.perf_counter() - t0) * 1000)
        await _save_memory(request.session_id, request.query, answer)
        return {"code": 0, "data": {"answer": answer, "total_ms": total_ms}}
    except Exception as e:
        return {"code": 1, "data": {"error": str(e)}}


@chat_router.post("/stream", summary="LLM 对话（流式 SSE）")
async def chat_stream(request: ChatRequest):
    async def sse():
        llm = await get_llm_client()
        t0 = time.perf_counter()
        first = False
        chunks: list[str] = []
        try:
            prompt = await _build_prompt(request.session_id, request.query)
            print(f"  ⏱  [chat-stream] prompt={len(prompt)}chars  overhead={(time.perf_counter()-t0)*1000:.0f}ms")
            t_llm = time.perf_counter()
            async for token in llm.stream(
                prompt=prompt,
                system_prompt=request.system_prompt or None,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            ):
                if not first:
                    first = True
                    print(f"  ⏱  [chat-stream] llm_first_token={(time.perf_counter()-t_llm)*1000:.0f}ms")
                    yield f"event: first_token\ndata: {json.dumps({'ms': round((time.perf_counter() - t0) * 1000)})}\n\n"
                chunks.append(token)
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
            answer = "".join(chunks)
            await _save_memory(request.session_id, request.query, answer)
            total_ms = round((time.perf_counter() - t0) * 1000)
            yield f"event: done\ndata: {json.dumps({'total_ms': total_ms})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
    return StreamingResponse(sse(), media_type="text/event-stream")


# ============================================================
# 会话记忆 (prefix /api/ai/memory)
# ============================================================
memory_router = APIRouter(prefix="/api/ai/memory", tags=["会话记忆"])


@memory_router.get("/history", summary="查看对话历史")
async def get_history(session_id: str = Query(..., description="会话 ID")) -> dict:
    try:
        mgr = await get_memory_manager()
        memory = await mgr.get_memory(session_id)
        return {"code": 0, "data": {"session_id": session_id, "turns": memory.turns, "count": len(memory.turns)}}
    except Exception as e:
        return {"code": 1, "data": {"error": str(e)}}


@memory_router.get("/tickets", summary="待派单列表")
async def list_pending() -> dict:
    try:
        mgr = await get_memory_manager()
        sessions = await mgr.list_pending_tickets()
        return {"code": 0, "data": {"pending": sessions, "count": len(sessions)}}
    except Exception as e:
        return {"code": 1, "data": {"error": str(e)}}


@memory_router.delete("/clear-all", summary="清除所有会话")
async def clear_all() -> dict:
    try:
        mgr = await get_memory_manager()
        count = await mgr.clear_all()
        return {"code": 0, "data": {"cleared": count, "message": f"已清除 {count} 个会话"}}
    except Exception as e:
        return {"code": 1, "data": {"error": str(e)}}


@memory_router.delete("/clear", summary="清除对话历史")
async def clear_history(session_id: str = Query(..., description="会话 ID")) -> dict:
    try:
        mgr = await get_memory_manager()
        await mgr.clear(session_id)
        return {"code": 0, "data": {"session_id": session_id, "message": "已清除"}}
    except Exception as e:
        return {"code": 1, "data": {"error": str(e)}}
