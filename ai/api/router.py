"""
统一 API 路由
  /api/ai/qa/*    诊断 Agent
  /api/ai/chat/*  纯 LLM 对话
  /api/ai/memory/* 会话记忆
  /api/ai/task/*  任务 Agent
"""
import json
import time
import os
from pathlib import Path
from typing import Dict, List
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai.core.logging import get_logger

logger = get_logger(__name__)

from ai.core import get_llm_client, get_memory_manager
from ai.config import get_ai_config
from ai.agents.AiDiagnosisPlatform.pipeline import (
    get_diagnosis_platform, AiDiagnosisPlatform, DiagnosisRequest,
)

# 注：app.core.* 的导入（decode_token / get_user_with_roles / SessionLocal / Ticket）
# 均为惰性（函数内），避免在 AI 进程启动时触发 backend app/__init__.py 全量装配。

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


def _current_user(request: Request) -> tuple[str, bool]:
    """从 Authorization 头解出 (username, is_admin)；无效/缺失返回 ('', False)。"""
    from app.core.security import decode_token  # 惰性：避免启动期触发 backend 装配
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return "", False
    payload = decode_token(token)
    if not payload:
        return "", False
    username = (payload.get("sub") or "").strip()
    if not username:
        return "", False
    is_admin = False
    try:
        from app.core.database import get_user_with_roles
        user = get_user_with_roles(username) or {}
        perms = user.get("permissions") or []
        is_admin = "admin" in perms
    except Exception:
        pass
    return username, is_admin


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
    request: Request,
    body: QASubmitRequest,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    username, _ = _current_user(request)
    # 本地测试 / 无 token 时用 debug 用户名
    if not username and get_ai_config().debug_assign_to_admin:
        username = "debug_test_user"
    try:
        result = await pipeline.submit(session_id=body.session_id, created_by=username)
        if "code" not in result:
            result["code"] = 0
        return result
    except Exception as e:
        logger.error(f"转工单接口异常: session_id={body.session_id}, user={username}, error={e}", exc_info=True)
        return {"code": 1, "message": f"工单生成失败: {str(e)}"}


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

        # 如果已有提交的工单，自动将附件追加到工单
        last_ticket = state.get("last_submitted_ticket", {})
        if last_ticket and last_ticket.get("ticket_id"):
            pipeline = await get_pipeline()
            ok = await pipeline._append_to_ticket(session_id, attachments=saved)
            if ok:
                logger.info(f"上传附件已追加到工单: session={session_id[:8]}, files={filenames}")
    except Exception as e:
        logger.warning(f"上传附件处理失败: {e}")
    return {"code": 0, "data": {"saved": len(saved), "files": saved}}


@qa_router.get("/health", summary="健康检查")
async def qa_health() -> dict:
    from ai.config import validate_ai_config
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
            logger.debug(f"[chat-stream] prompt={len(prompt)}chars  overhead={(time.perf_counter()-t0)*1000:.0f}ms")
            t_llm = time.perf_counter()
            async for token in llm.stream(
                prompt=prompt,
                system_prompt=request.system_prompt or None,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            ):
                if not first:
                    first = True
                    logger.debug(f"[chat-stream] llm_first_token={(time.perf_counter()-t_llm)*1000:.0f}ms")
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


@memory_router.get("/tickets/all", summary="历史工单列表")
async def list_all_tickets(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(20, ge=1, le=200, description="返回条数"),
    status: str = Query("", description="按状态筛选: pending / in_progress / resolved / closed"),
    type_: str = Query("", alias="type", description="按类型筛选"),
) -> dict:
    """查询 tasks 表（source='ai'），分页返回所有历史工单"""
    try:
        from ai.core.task_adapter import task_to_dict
        from app.models.task import Task, TaskStatus, TaskType
        from app.core.db import SessionLocal
        from sqlalchemy import desc

        db = SessionLocal()
        try:
            q = db.query(Task).filter(Task.source == "ai")
            # status/type 字符串 → 枚举；非法值（如旧值 dispatched）降级为不过滤
            if status:
                try:
                    q = q.filter(Task.status == TaskStatus(status))
                except ValueError:
                    pass
            if type_:
                try:
                    q = q.filter(Task.task_type == TaskType(type_))
                except ValueError:
                    pass
            total = q.count()
            rows = q.order_by(desc(Task.created_at)).offset(skip).limit(limit).all()
            items = []
            for r in rows:
                d = task_to_dict(r)
                items.append({
                    "id": d["id"], "session_id": d["session_id"], "ticket_ai_id": d["ticket_ai_id"],
                    "title": d["title"], "description": d["description"], "type": d["type"],
                    "priority": d["priority"], "status": d["status"], "contact": d["contact"],
                    "location": d["location"], "robot_type": d["robot_type"],
                    "fault_code": d["fault_code"], "severity": d["severity"],
                    "attachments": d["attachments"], "diagnosis": d["diagnosis"],
                    "created_at": d["created_at"].isoformat() if d["created_at"] else None,
                    "updated_at": d["updated_at"].isoformat() if d["updated_at"] else None,
                })
            return {"code": 0, "data": {"total": total, "skip": skip, "limit": limit, "items": items}}
        finally:
            db.close()
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


# ============================================================
# 任务 Agent (prefix /api/ai/task)
# ============================================================
task_agent_router = APIRouter(prefix="/api/ai/task", tags=["AI任务助手"])


class TaskAnalyzeAPIRequest(BaseModel):
    task_id: str = Field(default="", description="工单 ID（仅 analyze 需要）")
    session_id: str = Field(..., description="对话 session")
    query: str = Field(default="", description="用户消息")
    username: str = Field(default="", description="当前用户名（chat 模式用于获取用户工单）")
    token: str = Field(default="", description="用户 JWT token（用于调后端 API 鉴权）")


class TaskListAPIRequest(BaseModel):
    username: str = Field(default="", description="当前用户")


class TaskSubmitAPIRequest(BaseModel):
    task_id: str = Field(..., description="工单 ID")
    session_id: str = Field(..., description="对话 session")
    final_solution: dict = Field(..., description="工程师编辑后的最终方案")
    resolution: str = Field(default="resolved")


@task_agent_router.post("/list", summary="列出当前用户待处理工单")
async def task_list(body: TaskListAPIRequest) -> dict:
    """列出当前用户的待处理任务（从 tasks 表查询，source='ai'）。

    查询策略：按 created_by 匹配用户名。
    status 过滤：默认排除 resolved/closed，只看待处理。
    """
    try:
        from ai.core.task_adapter import task_to_dict
        from app.models.task import Task, TaskStatus
        from app.core.db import SessionLocal
        db = SessionLocal()
        tickets_list = []
        try:
            q = db.query(Task).filter(
                ~Task.status.in_([TaskStatus.RESOLVED, TaskStatus.CLOSED])
            )
            # 按用户名匹配创建者
            if body.username:
                q = q.filter(Task.created_by == body.username)
            q = q.order_by(
                Task.priority.desc(), Task.created_at.desc()
            ).limit(50)
            for t in q.all():
                d = task_to_dict(t)
                tickets_list.append({
                    "task_id": str(t.id),
                    "title": d["title"],
                    "description": (d["description"] or "")[:100],
                    "priority": d["priority"],
                    "status": d["status"],
                    "type": d["type"],
                    "robot_type": d["robot_type"],
                    "created_at": t.created_at.isoformat() if t.created_at else "",
                    "has_attachments": bool(t.attachments),
                })
        finally:
            db.close()

        priority_counts = {}
        for t in tickets_list:
            p = t["priority"]
            priority_counts[p] = priority_counts.get(p, 0) + 1
        summary_parts = [f"你当前有 {len(tickets_list)} 个待处理工单"]
        if priority_counts:
            detail = "，".join(f"{v} 个{c}" for c, v in priority_counts.items())
            summary_parts.append(f"（{detail}）")

        return {
            "code": 0,
            "data": {
                "summary": "".join(summary_parts),
                "tickets": tickets_list,
                "total": len(tickets_list),
            },
        }
    except Exception as e:
        return {"code": 1, "message": f"加载工单列表失败: {str(e)}", "data": {"tickets": [], "total": 0}}


@task_agent_router.post("/analyze", summary="分析工单（非流式）")
async def task_analyze(body: TaskAnalyzeAPIRequest) -> dict:
    try:
        from ai.agents.AiTaskPlatform import get_task_agent, TaskAnalyzeRequest
        agent = await get_task_agent()
        draft = await agent.analyze(TaskAnalyzeRequest(
            task_id=body.task_id, session_id=body.session_id))
        resp_data = draft.model_dump()
        resp_data.update({
            "_trace": getattr(draft, "_trace", []),
            "_total_ms": getattr(draft, "_total_ms", 0),
        })
        return {"code": 0, "data": resp_data}
    except Exception as e:
        return {"code": 1, "message": str(e)}


@task_agent_router.post("/analyze/stream", summary="流式分析工单（SSE）")
async def task_analyze_stream(body: TaskAnalyzeAPIRequest):
    from ai.agents.AiTaskPlatform import get_task_agent, TaskAnalyzeRequest

    async def sse():
        t0 = time.perf_counter()
        first = False
        try:
            agent = await get_task_agent()
            async for event in agent.analyze_stream(TaskAnalyzeRequest(
                task_id=body.task_id, session_id=body.session_id)):
                ev_type = event["event"]
                if ev_type == "token":
                    if not first:
                        first = True
                        yield f"event: first_token\ndata: {json.dumps({'ms': round((time.perf_counter() - t0) * 1000)}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'token': event['data']}, ensure_ascii=False)}\n\n"
                elif ev_type == "result":
                    yield f"event: result\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                elif ev_type == "first_token":
                    # 已在 token 分支处理 first，这里跳过
                    pass
                else:
                    yield f"event: status\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'total_ms': round((time.perf_counter() - t0) * 1000)})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@task_agent_router.post("/submit", summary="提交方案")
async def task_submit(request: Request, body: TaskSubmitAPIRequest) -> dict:
    try:
        from ai.agents.AiTaskPlatform import get_task_agent, SolutionDraft
        agent = await get_task_agent()
        draft = SolutionDraft(**body.final_solution)
        result = await agent.submit(
            task_id=body.task_id,
            session_id=body.session_id,
            draft=draft,
            resolution=body.resolution,
        )
        return result
    except Exception as e:
        return {"code": 1, "message": str(e)}


@task_agent_router.post("/chat", summary="自由问答（非流式）")
async def task_chat(body: TaskAnalyzeAPIRequest) -> dict:
    """v2.0 自由问答：感知用户所有工单 + 诊断状态"""
    try:
        from ai.agents.AiTaskPlatform import get_task_agent
        agent = await get_task_agent()
        response = await agent.chat(
            session_id=body.session_id,
            query=getattr(body, 'query', ''),
            username=getattr(body, 'username', '') or "",
            token=getattr(body, 'token', '') or "",
        )
        return {"code": 0, "data": {"reply": response}, "_trace": agent._pop_trace()}
    except Exception as e:
        return {"code": 1, "message": str(e)}


@task_agent_router.post("/chat/stream", summary="自由问答（流式 SSE）")
async def task_chat_stream(body: TaskAnalyzeAPIRequest):
    """流式自由问答"""
    from ai.agents.AiTaskPlatform import get_task_agent

    async def sse():
        t0 = time.perf_counter()
        first = False
        try:
            agent = await get_task_agent()
            async for event in agent.chat_stream(
                session_id=body.session_id,
                query=getattr(body, 'query', ''),
                username=getattr(body, 'username', '') or "",
                token=getattr(body, 'token', '') or "",
            ):
                ev_type = event["event"]
                if ev_type == "token":
                    if not first:
                        first = True
                        yield f"event: first_token\ndata: {json.dumps({'ms': round((time.perf_counter() - t0) * 1000)}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'token': event['data']}, ensure_ascii=False)}\n\n"
                elif ev_type == "first_token":
                    pass
                else:
                    yield f"event: status\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'total_ms': round((time.perf_counter() - t0) * 1000)})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@task_agent_router.get("/health", summary="健康检查")
async def task_agent_health() -> dict:
    return {"status": "ok", "service": "ai-task-agent"}
