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


class TicketConfirmRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    overrides: dict = Field(default_factory=dict, description="用户修改后的字段")


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
                elif ev_type == "title":
                    yield f"event: title\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
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
        # 主路径：Redis/内存中 agent_state 有效 → 直接组装工单
        if agent_state.get("phase") in ("escalated", "resolved"):
            pipeline = await get_pipeline()
            ticket = await pipeline.get_ticket(session_id)
            # ticket_id 默认是 "AI-..." 字符串，但前端催办/上报/评论/撤回依赖
            # 任务服务的数字 Task.id。此处查库取真实 id 覆盖，避免按钮报「工单号缺失」。
            task_id = _resolve_ai_task_id(session_id)
            if task_id is not None:
                ticket["ticket_id"] = task_id
            return {"code": 0, "data": ticket}

        # 降级：MySQL tasks 表中已有记录但 Redis 内存丢失（Redis 重启/过期等场景）
        from ai.core.task_adapter import task_to_dict
        from app.models.task import Task
        from app.core.db import SessionLocal
        db = SessionLocal()
        try:
            task = db.query(Task).filter(
                Task.source == "ai",
                Task.external_id.like(f"{session_id}%"),
            ).order_by(Task.id.desc()).first()
            if task:
                logger.info(f"MySQL 降级命中工单: session_id={session_id[:20]}, task_id={task.id}")
                return {"code": 0, "data": task_to_dict(task)}
        finally:
            db.close()

        return {"code": 1, "message": "该会话尚未生成工单"}
    except Exception as e:
        return {"code": 1, "message": str(e)}


def _resolve_ai_task_id(session_id: str):
    """按 session_id 查 ai 来源 Task，返回数字 id（供前端作为工单号）。

    优先按 (source, external_id) 精确匹配；external_id 可能因 ticket_seq 带了
    '#N' 后缀或超长走 sha1，故同时按 LIKE 兜底。
    """
    try:
        from ai.core.task_adapter import _external_id_for
        from app.models.task import Task
        from app.core.db import SessionLocal
        db = SessionLocal()
        try:
            exact = db.query(Task).filter(
                Task.source == "ai",
                Task.external_id == _external_id_for(session_id),
            ).order_by(Task.id.desc()).first()
            if exact:
                return exact.id
            like = db.query(Task).filter(
                Task.source == "ai",
                Task.external_id.like(f"{session_id[:64]}%"),
            ).order_by(Task.id.desc()).first()
            return like.id if like else None
        finally:
            db.close()
    except Exception:
        return None



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


@qa_router.post("/ticket/prepare", summary="生成工单草稿（路径1：按钮转工单）")
async def prepare_ticket(
    request: Request,
    body: QASubmitRequest,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    try:
        result = await pipeline.prepare_ticket(session_id=body.session_id)
        return {"code": 0, "data": result}
    except Exception as e:
        logger.error(f"prepare_ticket 异常: {e}", exc_info=True)
        return {"code": 1, "message": str(e)}


@qa_router.post("/ticket/confirm", summary="确认提交工单（路径1：弹窗确认后）")
async def confirm_ticket(
    request: Request,
    body: TicketConfirmRequest,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    try:
        return await pipeline.confirm_submit(
            session_id=body.session_id, overrides=body.overrides,
        )
    except Exception as e:
        logger.error(f"confirm_ticket 异常: {e}", exc_info=True)
        return {"code": 1, "message": str(e)}


@qa_router.get("/ticket/draft", summary="获取待确认草稿（前端轮询兜底）")
async def get_draft(
    session_id: str = Query(..., description="会话 ID"),
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    try:
        return await pipeline.get_draft(session_id)
    except Exception as e:
        logger.error(f"get_draft 异常: {e}", exc_info=True)
        return {"code": 1, "message": str(e)}


@qa_router.post("/upload", summary="上传附件")
async def upload_files(
    session_id: str = Form(..., description="会话 ID"),
    files: List[UploadFile] = File(..., description="附件文件"),
):
    from app.utils.minio_client import minio_client

    # ── 1. 上传到 MinIO ──
    saved = []
    raw_bytes: list[tuple] = []  # (filename, bytes) 暂存供 VLM
    for f in files:
        content = await f.read()
        raw_bytes.append((f.filename, content))
        object_path = f"helpdesk/{session_id}/{f.filename}"
        minio_client.upload_bytes(content, object_path, content_type=f.content_type)
        url = minio_client.get_presigned_url(object_path, expires_minutes=1440)
        saved.append({"filename": f.filename, "size": len(content), "path": url})
    filenames = "、".join(s["filename"] for s in saved)

    # ── 2. 图片描述：VLM 看图层 → 注入会话上下文 ──
    image_desc = ""
    try:
        from ai.agents.AiTaskPlatform.attachments.parser import _is_image_file
        from ai.core import get_llm_client
        import base64
        _MIME_MAP = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".png": "image/png", ".bmp": "image/bmp",
                     ".gif": "image/gif", ".webp": "image/webp"}
        data_uris = []
        for fname, raw in raw_bytes:
            if _is_image_file(fname, fname):
                try:
                    ext = Path(fname).suffix.lower()
                    mime = _MIME_MAP.get(ext, "image/png")
                    b64 = base64.b64encode(raw).decode()
                    data_uris.append((fname, f"data:{mime};base64,{b64}"))
                except Exception:
                    pass
        if data_uris:
            llm = await get_llm_client()
            names = ", ".join(n for n, _ in data_uris)
            uris = [u for _, u in data_uris]
            desc = await llm.complete_vision(
                prompt=f"描述图片 {names}。这是 AGV/AMR 调度系统的截图。请描述画面内容、关键数据、异常信号和人工标注。用工程师口吻，客观描述，不下结论。",
                images=uris,
                system_prompt="你是 AGV/AMR 调度系统的操作专家。仔细看图，客观描述。不诊断，不下结论。",
                max_tokens=400,
                temperature=0.2,
            )
            image_desc = desc.strip()
    except Exception as e:
        logger.warning(f"上传图片描述失败: {e}")

    # ── 3. 写入会话记忆 ──
    mgr = await get_memory_manager()
    if image_desc:
        await mgr.add_turn(session_id, "user",
                           f"我上传了 {len(saved)} 个文件：{filenames}。图片主要内容为：{image_desc}")
        await mgr.add_turn(session_id, "assistant",
                           f"已收到 {len(saved)} 个文件，已附到本次会话中。")
    else:
        await mgr.add_turn(session_id, "user", f"[上传了附件] {filenames}")
        await mgr.add_turn(session_id, "assistant", f"已收到 {len(saved)} 个文件，已附到本次会话中。")

    # ── 4. agent_state.attachments + 追加到已提交工单 ──
    try:
        memory = await mgr.get_memory(session_id)
        state = memory.metadata.get("agent_state", {})
        existing = state.get("attachments", [])
        state["attachments"] = existing + saved
        memory.metadata["agent_state"] = state
        await mgr.save_memory(memory)

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
    query: str = Field(..., min_length=1, max_length=200000)
    max_tokens: int = Field(default=2000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    system_prompt: str = Field(default="", max_length=20000, description="可选系统提示词")


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
    keyword: str = Query("", description="模糊搜索标题/描述"),
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
            if keyword:
                q = q.filter(Task.title.contains(keyword) | Task.description.contains(keyword))
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
task_agent_router = APIRouter(prefix="/api/ai/task", tags=["小U"])



class TaskSubmitAPIRequest(BaseModel):
    task_id: str = Field(..., description="工单 ID")
    session_id: str = Field(..., description="对话 session")
    final_solution: dict = Field(..., description="工程师编辑后的最终方案")
    resolution: str = Field(default="resolved")


class SummarizeRequest(BaseModel):
    """后端触发摘要扫描（无参数 — AI 模块自动扫描所有活跃工单）"""


# ── v3.0 端点 ──

class TaskDiagnoseRequest(BaseModel):
    task_id: str = Field(..., description="工单 ID")


class TaskDiscussRequest(BaseModel):
    task_id: str = Field(..., description="工单 ID")
    query: str = Field(..., description="用户问题（如 @AI 帮我分析这个日志）")
    context: dict = Field(default_factory=dict, description="讨论上下文 {recent_comments: [{author, content}]}")


@task_agent_router.post("/diagnose", summary="诊断报告（[帮我分析] 按钮）")
async def task_diagnose(body: TaskDiagnoseRequest) -> dict:
    """全能力诊断 → 即时返回报告（不存库）"""
    try:
        from ai.agents.AiTaskPlatform import get_task_agent
        agent = await get_task_agent()
        result = await agent.diagnose(task_id=body.task_id)
        return {"code": 0, "data": result}
    except Exception as e:
        return {"code": 1, "message": str(e)}


@task_agent_router.post("/discuss", summary="@AI 讨论")
async def task_discuss(body: TaskDiscussRequest) -> dict:
    """@AI 讨论回复（带讨论上下文，按需调日志子Agent）→ 写 task_comments"""
    try:
        from ai.agents.AiTaskPlatform import get_task_agent
        agent = await get_task_agent()
        result = await agent.discuss(
            task_id=body.task_id,
            query=body.query,
            context=body.context,
        )
        return {"code": 0, "data": result}
    except Exception as e:
        return {"code": 1, "message": str(e)}


@task_agent_router.post("/summarize", summary="讨论摘要")
async def task_summarize(body: SummarizeRequest = SummarizeRequest()) -> dict:
    """后端触发 → AI 模块自动扫描所有活跃工单 → 逐条生成摘要 → 写 task_comments"""
    try:
        from ai.agents.AiTaskPlatform import get_task_agent
        agent = await get_task_agent()
        result = await agent.summarize_batch()
        return {"code": 0, "data": result}
    except Exception as e:
        return {"code": 1, "message": str(e)}


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


@task_agent_router.get("/health", summary="健康检查")
async def task_agent_health() -> dict:
    return {"status": "ok", "service": "ai-task-agent"}


# ============================================================
# 企业微信 (prefix /api/ai/wecom)
# ============================================================
wecom_router = APIRouter(prefix="/api/ai/wecom", tags=["企业微信"])


class WecomUpdateRequest(BaseModel):
    values: dict = Field(..., description="扁平格式的字段值，如 {\"项目名称\": \"新值\"}")


@wecom_router.get("/projects", summary="拉取全部项目")
async def wecom_pull_projects() -> dict:
    """从企业微信 Smartsheet 拉取 USP 项目表全部记录（已拍扁）"""
    try:
        from ai.integrations.wecom import WecomSmartsheetClient
        client = WecomSmartsheetClient()
        records = await client.pull_all()
        return {"code": 0, "data": {"total": len(records), "records": records}}
    except ValueError as e:
        return {"code": 1, "message": str(e)}
    except Exception as e:
        logger.error(f"wecom pull_projects 失败: {e}", exc_info=True)
        return {"code": 1, "message": f"拉取项目失败: {str(e)}"}


@wecom_router.get("/projects/search", summary="分页查询项目")
async def wecom_search_projects(
    offset: int = Query(0, ge=0, description="偏移量"),
    limit: int = Query(100, ge=1, le=1000, description="每页条数"),
) -> dict:
    """分页查询项目表，支持排序和字段过滤"""
    try:
        from ai.integrations.wecom import WecomSmartsheetClient
        client = WecomSmartsheetClient()
        data = await client.pull(limit=limit, offset=offset)
        return {"code": 0, "data": data}
    except ValueError as e:
        return {"code": 1, "message": str(e)}
    except Exception as e:
        logger.error(f"wecom search_projects 失败: {e}", exc_info=True)
        return {"code": 1, "message": f"查询项目失败: {str(e)}"}


@wecom_router.post("/projects/{record_id}", summary="更新单条项目")
async def wecom_update_project(record_id: str, body: WecomUpdateRequest) -> dict:
    """更新单条项目记录，values 为扁平格式"""
    try:
        from ai.integrations.wecom import WecomSmartsheetClient
        client = WecomSmartsheetClient()
        ok = await client.push_one(record_id, body.values)
        return {"code": 0, "data": {"record_id": record_id, "updated": ok}}
    except ValueError as e:
        return {"code": 1, "message": str(e)}
    except Exception as e:
        logger.error(f"wecom update_project 失败: {e}", exc_info=True)
        return {"code": 1, "message": f"更新项目失败: {str(e)}"}


@wecom_router.get("/health", summary="健康检查")
async def wecom_health() -> dict:
    try:
        from ai.config import get_ai_config
        cfg = get_ai_config()
        configured = bool(cfg.wecom_corpid and cfg.wecom_corpsecret)
        return {"status": "ok", "configured": configured}
    except Exception:
        return {"status": "ok", "configured": False}
