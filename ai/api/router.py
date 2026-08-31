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
import uuid
import asyncio
import collections
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, Request, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai.core.logging import get_logger

# name 固定 "AI"（直挂 file handler，propagate=False）：__name__（ai.api.router）
# 走 root propagate 链路，在 uvicorn reload spawn 的 worker 里实测丢日志
# （[sse] 行全部不进 ai.log），换 "AI" 后与 pipeline 同链路，已被生产日志验证。
logger = get_logger()

from ai.core import get_llm_client, get_memory_manager
from ai.config import get_ai_config
from ai.agents.AiDiagnosisPlatform.pipeline import (
    get_diagnosis_platform, AiDiagnosisPlatform, DiagnosisRequest,
)

# 注：app.core.* 的导入（decode_token / get_user_with_roles / SessionLocal / Ticket）
# 均为惰性（函数内），避免在 AI 进程启动时触发 backend app/__init__.py 全量装配。

# ============================================================
# SSE 心跳保活
# ============================================================
# 主 LLM thinking 阶段 10s+ 无任何 token 下发（reasoning_content 不对外输出），
# 长静默易被网关/微信 webview 按空闲连接杀掉 → 前端偶发「网络错误」「图片上传失败」。
# 心跳以 SSE 注释行 ": ping" 下发——对前端解析透明（只认 event:/data: 行），
# 却能让中间链路感知连接存活。每 _HEARTBEAT_SEC 秒一次。
_HEARTBEAT_SEC = 10.0
_HEARTBEAT_SSE = ": ping\n\n"


async def _heartbeat_agen(agen, interval: float = _HEARTBEAT_SEC):
    """包装异步生成器：interval 内无产出时插入 None 心跳标记。

    注意不能直接 asyncio.wait_for(anext())——超时会向生成器内部注入取消，
    把正在 await LLM HTTP 流的生产协程杀掉。这里用「anext 任务 vs sleep 任务」
    竞速，心跳触发时 anext 任务继续跑，绝不取消。
    """
    _DONE = object()

    async def _next():
        try:
            return await agen.__anext__()
        except StopAsyncIteration:
            return _DONE

    task = asyncio.create_task(_next())
    try:
        while True:
            hb = asyncio.create_task(asyncio.sleep(interval))
            done, _ = await asyncio.wait({task, hb}, return_when=asyncio.FIRST_COMPLETED)
            if hb in done:
                yield None
                continue
            hb.cancel()
            item = task.result()
            if item is _DONE:
                return
            yield item
            task = asyncio.create_task(_next())
    finally:
        # 外层 async-for 提前退出（客户端断连/CancelledError）时，
        # 取消仍在等 anext 的任务，恢复「断连即取消内层生成器」的原有语义。
        if not task.done():
            task.cancel()


# ============================================================
# 诊断 Agent (prefix /api/ai/qa)
# ============================================================
qa_router = APIRouter(prefix="/api/ai/qa", tags=["AI诊断"])


class QAAskRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")
    query: str = Field(..., min_length=1, max_length=500, description="用户输入")
    skip_retrieval: bool = Field(default=False, description="测试用：跳过 KB 检索")
    # 增量落库（后端 SSE 侧持久化 assistant 回复）：前端把已落库的会话 id 传进来，
    # 后端在流式中把 assistant 回复节流写入同会话 messages 表，刷新/切 Tab 也能从 DB 恢复。
    conversation_id: Optional[int] = Field(default=None, description="前端 call 会话 id（传了才启用后端落库）")
    assistant_message_id: Optional[int] = Field(default=None, description="已预建的 assistant 占位消息 id；不传则由后端创建")
    # 单写入方+幂等：True 时后端在流式开头确保本轮 user 消息已落库（前端已写则按
    # client_message_id 幂等复用，未写/写失败则代建），并创建 assistant 占位指向它
    # （parent_message_id）→ sequence 严格 user < assistant，消息顺序由落库顺序决定，
    # 与前端网络时序彻底解耦（根治 0827「回答跳到问题上方」）。
    # 旧前端不传此标志 → 行为完全不变（兼容灰度期新旧版本混跑）。
    persist_user_message: bool = Field(default=False, description="True=后端确保 user 消息已落库（幂等复用优先，缺失则代建）")
    client_message_id: Optional[str] = Field(default=None, max_length=64, description="本轮消息幂等键（前端本地乐观气泡 id）")


class QASubmitRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")
    username: str = Field(default="", description="前端显式传的当前登录用户（兜底：token 失效时用，token 有效则以 token sub 为准防伪造）")


class TicketAckRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    dispatch_id: str = Field(default="", description="派单系统内部工单 ID")
    status: str = Field(default="dispatched", description="派单状态")


class TicketConfirmRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    overrides: dict = Field(default_factory=dict, description="用户修改后的字段")
    username: str = Field(default="", description="前端显式传的当前登录用户（兜底：token 失效时用）")


async def get_pipeline() -> AiDiagnosisPlatform:
    return await get_diagnosis_platform()


def _current_user(request: Request) -> tuple[str, bool]:
    """从 Authorization 头解出 (username, is_admin)；无效/缺失返回 ('', False)。"""
    auth = request.headers.get("Authorization", "")
    return _current_user_from_header(auth)


def _current_user_from_header(authorization: str) -> tuple[str, bool]:
    """从 Authorization header 值解出 (username, is_admin)；无效/缺失返回 ('', False)。"""
    from app.core.security import decode_token  # 惰性：避免启动期触发 backend 装配
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
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
    qa_req: QAAskRequest,
    http_req: Request,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    username, _ = _current_user(http_req)
    # token 失效 → 返回 401，触发前端 fetchWithAuth 刷新重试，避免自动提单 created_by=""
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    qa_request = DiagnosisRequest(session_id=qa_req.session_id, query=qa_req.query,
                           skip_retrieval=qa_req.skip_retrieval, created_by=username)
    try:
        result = await pipeline.run_with_timeout(qa_request, timeout=30.0)
    except Exception as e:
        return {"code": 1, "message": f"系统错误: {str(e)}"}
    if "code" not in result:
        result["code"] = 0
    return result


@qa_router.post("/ask/stream", summary="流式问答（SSE）")
async def ask_question_stream(
    qa_req: QAAskRequest,
    http_req: Request,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
):
    username, _ = _current_user(http_req)
    # token 失效 → 在 SSE 流开启前返回 401，触发前端 fetchWithAuth 刷新重试。
    # 若放进 sse() 内部抛出，HTTP 状态已是 200，前端刷新逻辑无法触发 → 自动提单 created_by=""。
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    async def sse():
        from app.core.db import AsyncSessionLocal
        from app.modules.call.schemas.message import MessageCreate, MessageUpdate
        from app.modules.call.services.message_service import MessageService

        t0 = time.perf_counter()
        qa_request = DiagnosisRequest(session_id=qa_req.session_id, query=qa_req.query,
                           skip_retrieval=qa_req.skip_retrieval, created_by=username)
        first = False
        _sse_trace: list[str] = []
        _sse_token_count = 0
        import asyncio  # producer-consumer 解耦：Queue/create_task/CancelledError

        def _flush_tokens():
            nonlocal _sse_token_count
            if _sse_token_count > 0:
                _sse_trace.append(f"token({_sse_token_count}c)")
                _sse_token_count = 0

        # ── 后端增量落库准备（仅当前端传入 conversation_id 时启用）──
        persist_msg_id = qa_req.assistant_message_id
        user_msg_id: Optional[int] = None
        db = None

        # 流式开头统一落库本轮消息（单写入方+幂等）：
        #   1) user 消息：persist_user_message=True 时确保存在——前端已写（metadata 含
        #      client_message_id）则复用该行，否则代建。响应丢失后同键重发不会产生重复行。
        #   2) assistant 占位：parent_message_id 指向 user 行；同 user 下已有占位（上次
        #      重试遗留）则复用。sequence 按创建顺序递增 → DB 顺序天然「问题在上、回答在下」，
        #      与前端写入/网络时序彻底解耦。
        # 旧前端不传 persist_user_message → 走原逻辑（只建 assistant 占位），行为不变。
        if qa_req.conversation_id:
            try:
                db = AsyncSessionLocal()
                if persist_msg_id is None:
                    msg = await MessageService.create_message(db, MessageCreate(
                        conversation_id=qa_req.conversation_id,
                        role="assistant",
                        content="",
                        message_type="text",
                    ))
                    persist_msg_id = msg.id
                    yield f"event: message_created\ndata: {json.dumps({'message_id': persist_msg_id}, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.warning(f"[sse] 建 assistant 消息失败（降级为不持久化） sid={qa_req.session_id[:8]}: {e}")
                if db is not None:
                    await db.close()
                db = None
                persist_msg_id = None

        # ── producer-consumer 解耦 ──
        # pipeline 放到独立后台任务（producer），不受 SSE 客户端断连取消。
        # 客户端刷新/关页时 consumer 被取消，producer 仍在后台继续生成 + 增量落库，
        # 刷新后前端从 DB 即可读到完整回复（首 token 前断连也有占位消息兜底）。
        queue = asyncio.Queue()
        _SENTINEL = object()

        async def producer():
            acc = ""
            last_persist = 0.0
            PERSIST_MS = 0.8
            # 落库改为后台任务 + 串行锁：早期 fire-and-forget 用线程池踩坑
            # （工作线程 get_event_loop 抛异常被吞 → 落库从未执行），但用
            # asyncio.create_task 是安全的——不再 inline await 阻塞 token 转发
            # （inline await 时 DB 写稍慢会周期性卡流，前端表现为吐字卡顿）。
            _persist_tasks = set()
            _persist_lock = asyncio.Lock()

            async def _do_persist(content: str):
                nonlocal _persist_tasks
                # 重试 + 换 session：原 session 可能已失效（长流期间连接闲置被断）。
                # 最终落库失败曾被 warning 吞掉 → DB 停在节流写的部分内容
                # （0825 事故：补充轮只剩单字「请」）。
                for attempt, delay in enumerate((0.0, 0.5, 1.0)):
                    if delay:
                        await asyncio.sleep(delay)
                    session = db if attempt == 0 else AsyncSessionLocal()
                    try:
                        async with _persist_lock:
                            await MessageService.update_message(
                                session, persist_msg_id, MessageUpdate(content=content))
                        if session is not db:
                            try:
                                await session.close()
                            except Exception:
                                pass
                        return
                    except Exception as e:
                        logger.warning(f"[sse] 增量落库失败(第{attempt + 1}次) "
                                       f"sid={qa_req.session_id[:8]} msg_id={persist_msg_id}: {e}")
                        if session is not db:
                            try:
                                await session.close()
                            except Exception:
                                pass
                logger.error(f"[sse] 增量落库最终失败 sid={qa_req.session_id[:8]} "
                             f"msg_id={persist_msg_id} len={len(content)}")

            def _persist_bg(content: str, force: bool = False):
                """节流落库（后台执行，不阻塞流）。force=True 走同步等待。"""
                nonlocal last_persist
                now = time.perf_counter()
                if not force and (now - last_persist) < PERSIST_MS:
                    return
                last_persist = now
                task = asyncio.create_task(_do_persist(content))
                _persist_tasks.add(task)
                task.add_done_callback(_persist_tasks.discard)

            try:
                async for event in pipeline.run_stream(qa_request):
                    ev_type = event.get("event")
                    if ev_type == "token":
                        acc += event.get('data', '')
                        if db is not None and persist_msg_id is not None:
                            _persist_bg(acc)
                    elif ev_type == "status":
                        # 提交/补信息阶段清空 acc（系统话术会重新流式），保证 DB 与前端展示一致。
                        # 同时刷新节流计时：清空后第一个 token 距上次落库常超 0.8s，
                        # 不刷新会把「请」这类单字/超短前缀立即写进 DB，若流结束的
                        # 最终落库失败，DB 终态就停在单字（0825 生产事故）。
                        stage = event.get('data', {}).get('stage', '?')
                        if stage in ('need_info', 'need_fields', 'review', 'submit_failed'):
                            acc = ""
                            last_persist = time.perf_counter()
                    await queue.put(event)
                # 流结束：先排空后台节流任务再写终态。
                # 0825 事故（补充轮只剩单字「已」）：pipeline 兜底路径逐字符 yield，
                # 首字符触发 _persist_bg 的 create_task，其余字符被节流挡住；
                # 流结束的最终全量落库先 await 让出事件循环，锁队列里那个单字
                # task 后拿到锁执行，把完整内容覆盖回首字快照。先 gather 排空，
                # 保证终态写入永远是最后一次。
                if _persist_tasks:
                    await asyncio.gather(*_persist_tasks, return_exceptions=True)
                if db is not None and persist_msg_id is not None:
                    try:
                        await _do_persist(acc)
                    except Exception:
                        pass
                # 标题异步生成：流结束后等它落地（上限 8s），补发 event: title。
                # 前端 ChatPanel 靠这个事件刷新会话标题;异步化后 result.title 恒空,
                # 不补发前端就永远显示「新建会话」。超时/失败静默降级。
                try:
                    _title = await asyncio.wait_for(
                        pipeline.collect_title(qa_req.session_id), timeout=8.0)
                except Exception:
                    _title = ""
                if _title:
                    await queue.put({"event": "title", "data": {"title": _title}})
                await queue.put(_SENTINEL)
            except Exception as e:
                # 异常：保留已接收内容（同样先排空后台任务，防止单字快照后写覆盖）
                if _persist_tasks:
                    await asyncio.gather(*_persist_tasks, return_exceptions=True)
                if db is not None and persist_msg_id is not None and acc:
                    try:
                        await _do_persist(acc)
                    except Exception:
                        pass
                await queue.put(("__error__", str(e)))
            finally:
                # 等后台落库任务收尾后再关 DB，避免连接在写入中途被关闭
                if _persist_tasks:
                    await asyncio.gather(*_persist_tasks, return_exceptions=True)
                if db is not None:
                    await db.close()

        producer_task = asyncio.create_task(producer())

        # ── 打字机平滑：上游（中转站 claude/gpt）token 常以中块突发到达
        # （~40字/块、间隔 700-1000ms），直接转发会「卡一下出一坨」。
        # 这里把 token 入队，按固定节奏小块重放（40ms/12字上限，300字/秒），
        # 突发被摊平；小模型/真流式场景下块都低于阈值，基本直通。
        _tok_buf = collections.deque()
        _last_emit = 0.0
        _PACER_INTERVAL = 0.04
        _PACER_CHARS = 12

        try:
            # consumer 心跳保活：producer（LLM thinking）长时间无产出时插入 ": ping"
            async def _drain():
                while True:
                    event = await queue.get()
                    yield event
                    if event is _SENTINEL or (isinstance(event, tuple) and len(event) == 2 and event[0] == "__error__"):
                        return

            async def _drain_paced():
                """按节奏小块重放缓冲中的 token（每次最多 _PACER_CHARS 字）。"""
                nonlocal _last_emit
                while _tok_buf:
                    now = time.perf_counter()
                    wait = _PACER_INTERVAL - (now - _last_emit)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    out = []
                    while _tok_buf and len(out) < _PACER_CHARS:
                        out.append(_tok_buf.popleft())
                    _last_emit = time.perf_counter()
                    yield f"data: {json.dumps({'token': ''.join(out)}, ensure_ascii=False)}\n\n"

            async for event in _heartbeat_agen(_drain()):
                if event is None:
                    yield _HEARTBEAT_SSE
                    continue
                if event is _SENTINEL:
                    if _tok_buf:
                        _rest = ''.join(_tok_buf)
                        _tok_buf.clear()
                        yield f"data: {json.dumps({'token': _rest}, ensure_ascii=False)}\n\n"
                    break
                if isinstance(event, tuple) and len(event) == 2 and event[0] == "__error__":
                    if _tok_buf:
                        _rest = ''.join(_tok_buf)
                        _tok_buf.clear()
                        yield f"data: {json.dumps({'token': _rest}, ensure_ascii=False)}\n\n"
                    err_msg = event[1]
                    yield f"data: {json.dumps({'token': f'[AI 服务异常: {err_msg[:80]}]'}, ensure_ascii=False)}\n\n"
                    _flush_tokens()
                    logger.error(f"[sse] sid={qa_req.session_id[:8]} q={qa_req.query[:80]} "
                                 f"→ {' | '.join(_sse_trace)} | ERROR: {err_msg}")
                    yield f"event: error\ndata: {json.dumps({'error': err_msg})}\n\n"
                    break
                ev_type = event.get("event")
                if ev_type == "token":
                    if not first:
                        first = True
                        yield f"event: first_token\ndata: {json.dumps({'ms': round((time.perf_counter() - t0) * 1000)}, ensure_ascii=False)}\n\n"
                    _sse_token_count += len(event.get('data', ''))
                    _tok_buf.extend(event.get('data', ''))
                    async for _sse in _drain_paced():
                        yield _sse
                elif ev_type == "result":
                    # 流收尾前把剩余缓冲一次性放出（不拖尾）
                    if _tok_buf:
                        _rest = ''.join(_tok_buf)
                        _tok_buf.clear()
                        yield f"data: {json.dumps({'token': _rest}, ensure_ascii=False)}\n\n"
                    _flush_tokens()
                    _sse_trace.append(f"result")
                    yield f"event: result\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                elif ev_type == "title":
                    yield f"event: title\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                else:
                    _flush_tokens()
                    stage = event.get('data', {}).get('stage', '?')
                    _sse_trace.append(f"status{{{stage}}}")
                    yield f"event: status\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            _flush_tokens()
            total_ms = round((time.perf_counter() - t0) * 1000)
            logger.info(f"[sse] sid={qa_req.session_id[:8]} q={qa_req.query[:80]} "
                        f"→ {' | '.join(_sse_trace)} | done({total_ms}ms)")
            yield f"event: done\ndata: {json.dumps({'total_ms': total_ms})}\n\n"
        except asyncio.CancelledError:
            # 客户端断连（刷新/关页）：consumer 取消，但 producer 后台继续生成 + 落库。
            # 不 await producer_task（会被取消），让它独立完成；刷新后前端从 DB 读到完整回复。
            logger.info(f"[sse] 客户端断连，producer 后台继续 sid={qa_req.session_id[:8]}")
            raise

    return StreamingResponse(sse(), media_type="text/event-stream")


@qa_router.post("/submit", summary="生成工单并存库")
async def submit_ticket(
    request: Request,
    body: QASubmitRequest,
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    username, _ = _current_user(request)  # token 解析（有效时权威，防伪造）
    if not username:
        username = (getattr(body, "username", "") or "").strip()  # token 失效 → 用前端显式传的兜底
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
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
        # 主路径：已提交工单(phase=escalated/resolved)——历史工单详情全部命中此分支。
        # DB(tasks 表)在 submit 时已通过 upsert_task 持久化完整工单快照(含 diagnosis/
        # 类型专属字段/special_notes/attachments/发起人/处理人等)，task_to_dict 一次查库
        # 即可还原全部字段，ticket_id 即数字 Task.id(前端催办/上报/评论/撤回依赖)。
        # 不再调 pipeline.get_ticket→_build_ticket：后者每次都用 LLM 重新生成工单(单次
        # 推理 2-3s，占 TTFB 98%)，且 submit 后 _reset_state_after_submit 已清空诊断状态，
        # 重跑出的 diagnosis 反而为空；其生成结果几乎全被 DB 字段覆盖，纯冗余。
        if agent_state.get("phase") in ("escalated", "resolved"):
            from ai.core.task_adapter import task_to_dict
            task = _resolve_ai_task(session_id)
            if task is not None:
                logger.info(f"工单详情命中 DB 快照(跳过 LLM 生成): session_id={session_id[:20]}, task_id={task.id}")
                return {"code": 0, "data": task_to_dict(task)}
            # phase 标记已提交但 DB 行缺失：数据异常(submit 必然写过 DB)。
            # 不再用 LLM 兜底——submit 后诊断状态已清空、Redis 会话可能已过期，
            # LLM 现编出的工单既残缺又与真实数据不一致，反而误导。直接报错。
            logger.warning(f"工单详情异常: phase 标记已提交但 DB 无行, session_id={session_id[:20]}")
            return {"code": 1, "message": "工单数据异常（未在系统中找到对应记录），请联系管理员核查"}

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


def _resolve_ai_task(session_id: str):
    """按 session_id 查 ai 来源 Task，返回 Task 对象（供取数字 id + 发起人/处理人）。

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
                return exact
            return db.query(Task).filter(
                Task.source == "ai",
                Task.external_id.like(f"{session_id[:64]}%"),
            ).order_by(Task.id.desc()).first()
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
    username, _ = _current_user(request)  # token 解析（有效时权威，防伪造）
    if not username:
        username = (getattr(body, "username", "") or "").strip()  # token 失效 → 用前端显式传的兜底
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    try:
        return await pipeline.confirm_submit(
            session_id=body.session_id, overrides=body.overrides, created_by=username,
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


@qa_router.delete("/ticket/draft", summary="取消确认：清除待确认草稿（用户关闭/放弃提单时调用）")
async def clear_draft(
    session_id: str = Query(..., description="会话 ID"),
    pipeline: AiDiagnosisPlatform = Depends(get_pipeline),
) -> dict:
    try:
        return await pipeline.clear_draft(session_id)
    except Exception as e:
        logger.error(f"clear_draft 异常: {e}", exc_info=True)
        return {"code": 1, "message": str(e)}


async def _upload_events(
    session_id: str,
    files: List[UploadFile],
    message: str,
    authorization: str,
):
    """上传核心流水线，逐步产出内部事件（供 SSE / JSON 两种传输复用）。

    事件流：file_saved → vision_token* → vision_done → memory_written
    附带文字时的流式诊断（event: token/status/result）由调用方在
    memory_written 之后自行驱动（SSE 逐条转发，JSON 收集）。
    """
    from ai.core.minio_client import minio_client
    from ai.agents.AiTaskPlatform.attachments.parser import _is_image_file
    from ai.core import get_llm_client
    import base64

    _bucket = get_ai_config().minio_bucket
    t_upload_start = time.perf_counter()
    logger.info(
        f"[upload] 收到上传请求: session={session_id[:12]}, "
        f"file_count={len(files)}, "
        f"filenames={'、'.join(f.filename for f in files)}"
    )

    # ── 0. 确保目标 bucket 存在 ──
    try:
        minio_client.create_bucket(_bucket)
    except Exception as e:
        logger.warning(f"确保 bucket {_bucket} 存在失败: {e}（若桶实际存在可忽略）")

    # ── 1. 上传到 MinIO（异步并发，逐文件回执） ──
    # minio_client.upload_bytes / get_presigned_url 是同步阻塞调用，裸调会卡住整个事件循环，
    # 导致上传期间该进程上其它 SSE/问答请求全部阻塞（TTFB 被拉到 20s+）。
    # 用 asyncio.to_thread 把每个文件的上传+签名挪到线程池，并用 gather 并发；逐文件 yield
    # file_saved，让前端几乎立刻看到第一个文件回执（TTFB ≈ 单文件上传耗时，而非 Σ）。
    import asyncio as _asyncio
    saved: list[dict] = []
    raw_bytes: list[tuple] = []  # (filename, bytes) 暂存供 VLM

    def _upload_one(f_content: bytes, f_ct: str, fname: str) -> dict:
        # 对象名加唯一后缀：截图工具等场景同名文件（如 image.png）跨多次发送时，
        # 若直接用原文件名，同一 session 目录下 object_path 相同 → MinIO 覆盖写 →
        # 历史消息回显串成同一张图。filename 保留原始可读名供前端匹配/展示，
        # object_path 用唯一名保证每次上传落独立对象（跨发送也不冲突）。
        stem, ext = os.path.splitext(fname)
        uniq = uuid.uuid4().hex[:8]
        object_name = f"{stem}_{uniq}{ext}" if stem else f"{uniq}{ext}"
        object_path = f"{_bucket}/{session_id}/{object_name}"
        minio_client.upload_bytes(f_content, object_path, content_type=f_ct, raise_on_error=True)
        url = minio_client.get_presigned_url(object_path, expires_minutes=1440)
        return {"filename": fname, "size": len(f_content), "path": url, "object_path": object_path}

    async def _upload_one_async(f: UploadFile) -> dict:
        content = await f.read()
        raw_bytes.append((f.filename, content))
        try:
            return await _asyncio.to_thread(_upload_one, content, f.content_type, f.filename)
        except Exception as e:
            logger.error(f"附件上传到 MinIO 失败: {f.filename} -> {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=(
                    f"文件「{f.filename}」上传失败：{e}。"
                    f"请检查 MinIO 是否可达、桶 {_bucket} 是否存在、凭据是否正确。"
                ),
            )

    # 并发上传；逐个完成后立即 yield file_saved（单文件回执，前端可即时展示）
    upload_tasks = [_upload_one_async(f) for f in files]
    for coro in _asyncio.as_completed(upload_tasks):
        item = await coro
        saved.append(item)
        # 单文件 file_saved：saved 字段为当前已完成的那一个，filenames 为该文件名
        # （前端 onFileSaved 按累计已保存项渲染；保留与原批量事件兼容的字段名）
        yield {"event": "file_saved", "saved": [item], "filenames": item["filename"]}
    # 兜底：若无文件（理论不会），补一个空 file_saved 保持事件序列完整
    if not saved:
        yield {"event": "file_saved", "saved": [], "filenames": ""}
    filenames = "、".join(s["filename"] for s in saved)
    logger.info(f"[upload] 文件已保存: session={session_id[:12]}, saved={len(saved)}")

    # ── 2. 图片描述：VLM 流式看图 ──
    image_desc = ""
    _vlm_full = ""   # 要点+回应（用户可见的完整回执）
    _vlm_reply = ""  # 【回应】段单独留存，供 ack 拼接
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
                logger.info(f"[upload] 图片编码: name={fname}, ext={ext}, raw_bytes={len(raw)}, b64_len={len(b64)}")
            except Exception as e:
                logger.warning(f"[upload] 图片编码失败: name={fname}, error={e}")
    logger.info(
        f"[upload] 图片检测: session={session_id[:12]}, "
        f"total_files={len(files)}, image_files={len(data_uris)}, "
        f"non_image_files={len(files) - len(data_uris)}"
    )
    if data_uris:
        # 降级兜底：视觉模型未配置（本地/开发环境常见）时，跳过 VLM 图片分析，
        # 直接按「无图片分析」走后续回执逻辑，避免 stream_vision 打到空 base_url
        # 卡在连接超时（connect=45s × 3 次重试）导致前端一直「正在分析图片」。
        # 生产环境配置了 VISION_BASE_URL / VISION_API_KEY 后，此分支不生效，照常看图。
        _vision_cfg = get_ai_config()
        _vision_ready = bool(_vision_cfg.vision_base_url and _vision_cfg.vision_api_key)
        if not _vision_ready:
            logger.warning(
                f"[upload] 视觉模型未配置（VISION_BASE_URL/VISION_API_KEY 缺失），"
                f"跳过图片分析走降级回执: session={session_id[:12]}, "
                f"image_count={len(data_uris)}"
            )
            _names = ", ".join(n for n, _ in data_uris)
            # 降级回执：图片已正常保存，仅跳过「看内容」这一步；文案区别于非图片文件
            # 的「暂不支持解析」提示，避免误导用户以为是图片格式问题。
            _vlm_full = (
                f"已收到 {len(saved)} 个文件（{filenames}）。"
                f"图片已保存，当前环境未启用图片内容分析，"
                f"后续提单时这些图片会作为接单人处理工单的参考附件。"
            )
            yield {"event": "vision_start", "names": _names}
            yield {"event": "vision_done", "desc": ""}
        else:
            t_vlm = time.perf_counter()
            llm = await get_llm_client()
            names = ", ".join(n for n, _ in data_uris)
            uris = [u for _, u in data_uris]
            logger.info(
                f"[upload] 开始VLM调用: session={session_id[:12]}, "
                f"image_count={len(data_uris)}, names={names}"
            )
            # 拉取最近对话上下文，让 VLM 知道图片是在什么排查场景下截的
            vlm_context = ""
            try:
                mgr = await get_memory_manager()
                mem = await mgr.get_memory(session_id)
                recent = [t for t in mem.turns[-6:] if t.get("role") in ("user", "assistant")]
                if recent:
                    lines = []
                    for t in recent:
                        role = "用户" if t["role"] == "user" else "AI"
                        c = t.get("content", "")[:200]
                        lines.append(f"{role}：{c}")
                    vlm_context = "以下是最近的对话记录，供你理解图片背景：\n" + "\n".join(lines) + "\n"
            except Exception:
                pass
            yield {"event": "vision_start", "names": names}
            prompt = (
                f"分析图片 {names}。这是 AGV/AMR 调度系统的现场照片或界面截图。\n"
                f"{vlm_context}"
                f"请用**结构化要点**输出，总字数 ≤ 200 字：\n"
                f"- 画面类型（调度界面截图 / 设备现场照 / 文档表格 / 其他）\n"
                f"- 画面上可见的关键内容：界面名称/页面元素、文字标签、数值（含错误码、机器人ID）、"
                f"指示灯/设备状态——**逐字原样抄录，禁止推测补全**，看不清的写「（模糊）」\n"
                f"- 仅当画面上有明确的错误提示/告警标识时，原样转述该提示文字；画面正常就写「画面无报错提示」\n"
                f"要点之后另起一行写「【回应】」加一句话（≤40 字），结合画面要点和上面的对话背景：\n"
                f"- 背景已明确在排查什么 → 自然接话（如「这个数值确实不对，先记下了」）\n"
                f"- 看不出用户目的 → 一句话问清意图（要查图上的报错，还是问这个界面怎么操作，还是其他情况）\n"
                f"- 画面无报错且没有对话背景 → 问「这是遇到什么问题了，还是想了解这个界面的配置？」\n"
                f"要点部分禁止推测故障原因；回应部分不要给排查步骤、不要下诊断结论。"
            )
            try:
                async for tok in llm.stream_vision(
                    prompt=prompt,
                    images=uris,
                    system_prompt=(
                        "你是 AGV/AMR 调度系统的图片转述员，只做客观转述、不做分析判断："
                        "把画面上真实可见的内容（文字、数值、状态、错误码）逐字抄录成要点，"
                        "总字数 ≤ 200 字。看不清的注明「（模糊）」，禁止编造画面上没有的信息，"
                        "禁止推测故障原因。最后按 prompt 要求在「【回应】」行用一句话回应用户。"
                    ),
                    max_tokens=600,
                    temperature=0.3,
                ):
                    image_desc += tok
                    yield {"event": "vision_token", "token": tok}
                image_desc = image_desc.strip()
                # 【回应】段拆分：要点进 upload_message（主模型只看纯转述，不被回应话术
                # 带偏），要点+回应进 ack_message（用户看到的回执，图片+反问自然衔接）
                _vlm_full = image_desc
                _vlm_reply = ""
                if "【回应】" in image_desc:
                    _body, _, _vlm_reply = image_desc.partition("【回应】")
                    image_desc = _body.strip()
                    _vlm_reply = _vlm_reply.strip()
                    _vlm_full = image_desc + (f"\n\n{_vlm_reply}" if _vlm_reply else "")
                vlm_ms = round((time.perf_counter() - t_vlm) * 1000)
                logger.info(
                    f"[upload] VLM调用完成: session={session_id[:12]}, "
                    f"elapsed={vlm_ms}ms, desc_len={len(image_desc)}, "
                    f"reply_len={len(_vlm_reply)}, "
                    f"desc_empty={not image_desc}, desc前80字={image_desc[:80]}"
                )
            except Exception as e:
                logger.error(f"[upload] VLM失败: {e}", exc_info=True)
                yield {"event": "vision_error", "error": str(e)}
            yield {"event": "vision_done", "desc": _vlm_full}
    else:
        logger.info(f"[upload] 无图片文件，跳过VLM: session={session_id[:12]}")
        yield {"event": "vision_done", "desc": ""}

    # ── 3. 写入会话记忆 + agent_state（失败不阻塞上传响应） ──
    ack_message = ""
    try:
        mgr = await get_memory_manager()
        t_mem = time.perf_counter()
        memory_before = await mgr.get_memory(session_id)
        turn_count_before = len(memory_before.turns)
        logger.info(
            f"[upload] 注入记忆前: session={session_id[:12]}, "
            f"turns={turn_count_before}, has_desc={bool(image_desc)}, "
            f"desc_len={len(image_desc)}"
        )
        # 上传轮的完整原文随附件条目持久化（metadata 不受 10 轮滑动窗口截断）：
        # memory 里的上传轮被滑出窗口后，_attach_chat_snapshot 用它重建合成轮
        # 插回 db 历史——否则附件 md 里丢失「我上传了…」轮，图片内联无锚点
        # （0825 事故：图全落末尾节）。created_at 口径对齐 MySQL（naive UTC）。
        _uploaded_at = datetime.utcnow().isoformat(timespec="seconds")
        if image_desc:
            upload_message = f"我上传了 {len(saved)} 个文件：{filenames}。图片主要内容为：{image_desc}"
        else:
            upload_message = f"[上传了附件] {filenames}"
        await mgr.add_turn(session_id, "user", upload_message)
        # 确认回执（assistant turn）：要点+【回应】完整版给用户（图片分析后自然
        # 反问意图）；upload_message 里仍只存纯要点——主模型消费的对话保持纯转述
        if _vlm_full:
            ack_message = _vlm_full
        else:
            exts = {Path(f["filename"]).suffix.lower() for f in saved}
            ext_str = "、".join(exts)
            ack_message = (
                f"已收到 {len(saved)} 个文件（{filenames}），"
                f"文件格式 {ext_str}。\n"
                f"我们暂不支持解析除图片以外的文件类型，"
                f"但如果您后续提单，这些文件将作为接单人处理工单的参考依据。"
            )
        await mgr.add_turn(session_id, "assistant", ack_message)
        # agent_state.attachments + collected_info.image_description（对齐旧 /upload 的完整落库）
        memory = await mgr.get_memory(session_id)
        state = memory.metadata.get("agent_state", {}) or {}
        existing = state.get("attachments", [])
        # 去重：按 object_path + filename 去重，避免同一文件重复上传/多次提单累积冗余
        _seen = {(a.get("object_path"), a.get("filename")) for a in existing if isinstance(a, dict)}
        _new = [s for s in saved if (s.get("object_path"), s.get("filename")) not in _seen]
        # VLM 摘要随附件条目落库（截 160 字）：_build_ticket 做附件取舍时，
        # 对话记录里的图片内容已被 sanitize_images 屏蔽，摘要是 LLM 判断
        # 「附件与本单问题是否相关」的唯一内容信号。多图批次共享同一段摘要。
        if image_desc:
            _new = [{**s, "desc": image_desc[:160]} for s in _new]
        _new = [{**s, "uploaded_at": _uploaded_at, "upload_message": upload_message}
                for s in _new]
        state["attachments"] = existing + _new
        if image_desc:
            ci = state.get("collected_info", {}) or {}
            prev = ci.get("image_description", "")
            ci["image_description"] = (prev + "\n" + image_desc).strip() if prev else image_desc
            state["collected_info"] = ci
        memory.metadata["agent_state"] = state
        await mgr.save_memory(memory)
        memory_after = await mgr.get_memory(session_id)
        logger.info(
            f"[upload] 注入记忆后: session={session_id[:12]}, "
            f"turns_before={turn_count_before}, turns_after={len(memory_after.turns)}, "
            f"attachments_after={len(state['attachments'])}, "
            f"mem_elapsed={(time.perf_counter() - t_mem) * 1000:.0f}ms"
        )
    except Exception as e:
        logger.error(f"[upload] 写记忆/agent_state 失败（不阻塞上传响应）: {e}", exc_info=True)
        ack_message = image_desc if image_desc else f"已收到 {len(saved)} 个文件（{filenames}）。"

    yield {"event": "memory_written", "ack_message": ack_message}

    total_ms = round((time.perf_counter() - t_upload_start) * 1000)
    logger.info(
        f"[upload] 上传完成: session={session_id[:12]}, "
        f"total_files={len(saved)}, filenames={filenames}, "
        f"has_image_desc={bool(image_desc)}, total_ms={total_ms}"
    )


@qa_router.post("/upload", summary="上传附件")
async def upload_files(
    session_id: str = Form(..., description="会话 ID"),
    files: List[UploadFile] = File(..., description="附件文件"),
    message: str = Form("", description="附带文字（可选，有则直接走诊断）"),
    authorization: str = Header(default="", alias="Authorization"),
    accept: str = Header(default="", alias="Accept"),
):
    """上传附件：带 Accept: text/event-stream 时走 SSE 流式（逐 token 推送
    VLM 描述 + 诊断），否则回退一次性 JSON（对齐旧 /upload 响应结构）。
    合并自原 /upload 与 /upload/stream。
    """
    wants_stream = "text/event-stream" in accept

    # ── 鉴权前置：在开启 SSE 流之前先校验 token ──
    # 若在此处校验失败，可直接返回真正的 401；若放到流开始之后再抛 HTTPException，
    # 会触发 "Caught handled exception, but response already started"（200 头已发出，
    # 无法再改成 401）。故此处提前校验。
    stream_username, _ = _current_user_from_header(authorization)
    if not stream_username:
        if wants_stream:
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
        # 非流式路径：非流式分支后面还有一次 401 校验，这里不处理以免改变行为
        pass

    # 附带文字时诊断用流式推理（与 /ask/stream 一致）；SSE 逐条转发、JSON 收集。
    # 注意：必须定义为 async generator（内部 await + yield 转发 run_stream 事件），
    # 调用方 async for 直接消费；若定义为 async def 返回 run_stream 生成器，
    # async for 拿到的会是 coroutine（报 _aiter_ 错误）；定义为同步 def 则 await 不合法。
    async def _run_diagnosis():
        # 鉴权已在进入 SSE 流之前（upload_files 开头）校验过；此处为兜底。
        # 注意：流开始后不能再抛 HTTPException（会触发 "response already started"），
        # 故此处改为抛普通异常，由 sse() 的 except Exception 转为 SSE error 事件。
        username, _ = _current_user_from_header(authorization)
        if not username:
            raise PermissionError("登录已过期，请重新登录")
        pipeline = await get_pipeline()
        request = DiagnosisRequest(
            session_id=session_id,
            query=message.strip(),
            created_by=username,
        )
        async for event in pipeline.run_stream(request):
            yield event

    async def sse():
        try:
            ack = None
            async for ev in _heartbeat_agen(_upload_events(session_id, files, message, authorization)):
                if ev is None:
                    yield _HEARTBEAT_SSE
                    continue
                if ev["event"] == "file_saved":
                    yield f"event: file_saved\ndata: {json.dumps({'saved': ev['saved'], 'filenames': ev['filenames']}, ensure_ascii=False)}\n\n"
                elif ev["event"] == "vision_start":
                    yield f"event: vision_start\ndata: {json.dumps({'names': ev['names']}, ensure_ascii=False)}\n\n"
                elif ev["event"] == "vision_token":
                    yield f"data: {json.dumps({'token': ev['token']}, ensure_ascii=False)}\n\n"
                elif ev["event"] == "vision_error":
                    yield f"event: vision_error\ndata: {json.dumps({'error': ev['error']}, ensure_ascii=False)}\n\n"
                elif ev["event"] == "vision_done":
                    yield f"event: vision_done\ndata: {json.dumps({'desc': ev['desc']}, ensure_ascii=False)}\n\n"
                elif ev["event"] == "memory_written":
                    ack = ev["ack_message"]
            # ── 附带文字 → 流式诊断；否则回执即可 ──
            if message.strip():
                # 先推一个 status 保活，避免 _run_diagnosis 启动前的间隙（get_memory+检索）
                # 导致 SSE 长时间无数据 → nginx proxy_read_timeout 断连 → 前端 NetworkError
                yield f"event: status\ndata: {json.dumps({'stage': 'diagnosing', 'round': 1}, ensure_ascii=False)}\n\n"
                result_payload = None
                try:
                    async for event in _heartbeat_agen(_run_diagnosis()):
                        if event is None:
                            yield _HEARTBEAT_SSE
                            continue
                        ev_type = event.get("event")
                        if ev_type == "token":
                            yield f"data: {json.dumps({'token': event.get('data', '')}, ensure_ascii=False)}\n\n"
                        elif ev_type == "result":
                            result_payload = event.get("data", {})
                        elif ev_type == "status":
                            yield f"event: status\ndata: {json.dumps(event.get('data', {}), ensure_ascii=False)}\n\n"
                    if result_payload is None:
                        result_payload = {}
                    if result_payload.get("ticket"):
                        logger.info(f"[upload-stream] 触发提单: session={session_id[:12]}")
                    yield f"event: result\ndata: {json.dumps(result_payload, ensure_ascii=False)}\n\n"
                except HTTPException:
                    raise
                except Exception as e:
                    logger.error(f"[upload-stream] 诊断失败: {e}", exc_info=True)
                    yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            else:
                # memory_written 已把 ack_message 写成 assistant turn；SSE 回执透传给前端
                if ack:
                    yield f"event: result\ndata: {json.dumps({'message': ack}, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'total_ms': 0}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            logger.info(f"[upload-stream] 客户端断连: session={session_id[:12]}")
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[upload-stream] 未知异常: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    if wants_stream:
        return StreamingResponse(sse(), media_type="text/event-stream")

    # ── 非流式回退：跑完同一生成器，收集成旧 JSON 响应 ──
    saved: list = []
    ack_message = ""
    ai_response = None
    try:
        async for ev in _upload_events(session_id, files, message, authorization):
            if ev["event"] == "file_saved":
                saved.extend(ev["saved"])  # 逐文件回执，需累积（不能覆盖）
            elif ev["event"] == "memory_written":
                ack_message = ev["ack_message"]
    except HTTPException as e:
        raise
    except Exception as e:
        logger.error(f"[upload] 非流式上传异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"上传失败：{e}")

    if message.strip():
        username, _ = _current_user_from_header(authorization)
        if not username:
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
        try:
            result_payload = {}
            async for event in _run_diagnosis():
                if event.get("event") == "result":
                    result_payload = event.get("data", {})
            ai_response = {
                "message": result_payload.get("message", ""),
                "action": result_payload.get("action", ""),
                "thinking": result_payload.get("thinking", ""),
                "ticket": result_payload.get("ticket"),
            }
        except Exception as e:
            logger.error(f"上传附带文字诊断失败: {e}", exc_info=True)
            ai_response = {"error": str(e)}

    return {
        "code": 0,
        "data": {
            "saved": len(saved), "files": saved,
            "ack_message": ack_message,
            "ai_response": ai_response,
        },
    }




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
            async for token in _heartbeat_agen(llm.stream(
                prompt=prompt,
                system_prompt=request.system_prompt or None,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )):
                if token is None:
                    yield _HEARTBEAT_SSE
                    continue
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
            # 先 yield 缺省 token（前端可见），方便定位是后端异常（而非前端空白）
            yield f"data: {json.dumps({'token': f'[AI 服务异常: {str(e)[:80]}]'}, ensure_ascii=False)}\n\n"
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
async def _redispatch_tip_for_log(log, user_map) -> Optional[str]:
    """按需求方案 §3.6 四分支规则生成派单结果提醒的一句话摘要（无提醒返回 None）。

    数据源：task_dispatch_log 最新一条（与 backend TicketService._redispatch_tip 口径一致）。
    """
    if log is None:
        return None
    assigned_name = user_map.get(log.assigned_id, log.assigned_id)
    preferred_id = log.preferred_id
    preferred_name = user_map.get(preferred_id, preferred_id) if preferred_id else None

    # ② 未派到指定人（简洁而礼貌的措辞，照顾用户情绪）
    if preferred_id and preferred_id != log.assigned_id:
        tip = f"很抱歉，您指定的【{preferred_name}】暂未采纳，已改派更合适的【{assigned_name}】处理"
    # ④ 拼音/近似名命中
    elif log.pinyin_match:
        tip = f"按拼音匹配到【{assigned_name}】（与输入【{preferred_name or assigned_name}】不同字），如非此人请更正"
    # ③ 同名命中
    elif log.name_collision:
        tip = f"指派人存在同名，已按评估选择【{assigned_name}】"
    else:
        tip = None

    # ① 画像不完整（可叠加追加）
    missing = ((log.profile or {}).get("missing") or []) if isinstance(log.profile, dict) else []
    if missing:
        suffix = "；该接单人画像不完整，待补充"
        tip = (tip + suffix) if tip else "该接单人画像不完整，待补充"
    return tip


async def list_all_tickets(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(20, ge=1, le=200, description="返回条数"),
    status: str = Query("", description="按状态筛选: pending / in_progress / resolved / closed"),
    type_: str = Query("", alias="type", description="按类型筛选"),
    keyword: str = Query("", description="模糊搜索标题/描述"),
    username: str = Query("", description="按创建者用户名过滤"),
    exclude_status: str = Query("", description="排除的状态，逗号分隔（如 closed）"),
) -> dict:
    """查询 tasks 表，分页返回所有历史工单（含 AI 生成与系统手动创建）
    
    当 username 参数不为空时，只返回该用户创建的工单（created_by 字段匹配）。
    支持 source='ai'（AI 生成）和 source='manual'（系统任务手动创建）两类工单。
    """
    try:
        from ai.core.task_adapter import task_to_dict
        from app.models.task import Task, TaskStatus, TaskType
        from app.core.db import SessionLocal
        from app.services.user_service import UserService
        from sqlalchemy import desc, func

        # username → 展示名（与任务服务 /api/tasks 一致的解析口径）
        user_map = UserService.get_user_map()

        db = SessionLocal()
        try:
            q = db.query(Task).filter(Task.source.in_(["ai", "manual"]))
            # 按创建者过滤（非 admin 只看自己的）
            if username:
                from app.core.user_identity import identity_keys
                keys = identity_keys(username)
                q = q.filter(Task.created_by.in_(keys) if keys else Task.created_by == username)
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
            # 排除指定状态（如「除已关闭外全部」）；非法值忽略
            if exclude_status:
                for s in [x.strip() for x in exclude_status.split(",") if x.strip()]:
                    try:
                        q = q.filter(Task.status != TaskStatus(s))
                    except ValueError:
                        pass
            # 各状态数量分布（口径：source + username，不含 status/type/keyword/exclude 等筛选），
            # 复用本接口一并返回，供前端各状态 Tab 计数与 badge 使用，无需额外统计接口
            stat_q = db.query(Task.status, func.count(Task.id)).filter(Task.source.in_(["ai", "manual"]))
            if username:
                from app.core.user_identity import identity_keys
                keys = identity_keys(username)
                stat_q = stat_q.filter(Task.created_by.in_(keys) if keys else Task.created_by == username)
            by_status = {s.value: 0 for s in TaskStatus}
            for st, cnt in stat_q.group_by(Task.status).all():
                key = st.value if isinstance(st, TaskStatus) else st
                if key in by_status:
                    by_status[key] = cnt
            active_total = sum(by_status.values()) - by_status.get("closed", 0)

            total = q.count()
            rows = q.order_by(desc(Task.created_at)).offset(skip).limit(limit).all()

            # 二次派单感知增强（M3）：批量取各工单最新一条派单日志 → redispatch_tip（避免 N+1）
            from app.models.task_dispatch_log import TaskDispatchLog
            _ids = [r.id for r in rows]
            tip_map: Dict[int, Optional[str]] = {}
            if _ids:
                _log_rows = db.query(TaskDispatchLog).filter(
                    TaskDispatchLog.task_id.in_(_ids)
                ).order_by(TaskDispatchLog.task_id.asc(), TaskDispatchLog.dispatch_round.desc()).all()
                _seen: set = set()
                for _lr in _log_rows:
                    if _lr.task_id in _seen:
                        continue
                    _seen.add(_lr.task_id)
                    tip_map[_lr.task_id] = _redispatch_tip_for_log(_lr, user_map)

            items = []
            for r in rows:
                d = task_to_dict(r)
                created_by = r.created_by or ""
                assigned_to = r.assigned_to or ""
                items.append({
                    "id": d["id"], "session_id": d["session_id"], "ticket_ai_id": d["ticket_ai_id"],
                    "title": d["title"], "description": d["description"], "type": d["type"],
                    "priority": d["priority"], "status": d["status"], "contact": d["contact"],
                    "project": d["project"], "source": d["source"],
                    "location": d["location"], "robot_type": d["robot_type"],
                    "fault_code": d["fault_code"], "severity": d["severity"],
                    "attachments": d["attachments"], "diagnosis": d["diagnosis"],
                    # task_to_dict 已把 created_at/updated_at 显式 isoformat 为字符串（与 deadline_at 同口径），
                    # 这里直接透传，不再二次 .isoformat()（字符串无此方法，会抛 AttributeError）
                    "created_at": d["created_at"] or None,
                    "updated_at": d["updated_at"] or None,
                    # 提单人 / 接单人（username + 展示名），供前端「提单人 → 接单人」指向性 UI 渲染
                    "created_by": created_by,
                    "created_by_name": user_map.get(created_by, created_by) if created_by else "",
                    "assigned_to": assigned_to,
                    "assigned_to_name": user_map.get(assigned_to, assigned_to) if assigned_to else "",
                    # 二次派单感知增强（M3）：派单结果提醒一句话摘要（无提醒为 null）
                    "redispatch_tip": tip_map.get(r.id) or None,
                })
            return {"code": 0, "data": {"total": total, "skip": skip, "limit": limit, "items": items,
                                        "by_status": by_status, "active_total": active_total}}
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
task_agent_router = APIRouter(prefix="/api/ai/task", tags=["U老师"])

class SummarizeRequest(BaseModel):
    """后端触发摘要扫描（无参数 — U老师 自动扫描所有活跃工单）"""

class TaskDiagnoseRequest(BaseModel):
    task_id: str = Field(..., description="工单 ID")

class TaskDiscussRequest(BaseModel):
    task_id: str = Field(..., description="工单 ID")
    query: str = Field(..., description="用户问题（如 @U老师 帮我分析这个日志）")
    context: dict = Field(default_factory=dict, description="讨论上下文 {recent_comments: [{author, content}]}")

@task_agent_router.post("/diagnose", summary="诊断报告（[帮我分析] 按钮）")
async def task_diagnose(body: TaskDiagnoseRequest) -> dict:
    """全能力诊断 → 即时返回报告（不存库）"""
    import logging, time
    logger = logging.getLogger("TASK_AGENT")
    t_start = time.perf_counter()
    logger.info(f"[diagnose] 入口: task_id={body.task_id}")
    try:
        from ai.agents.AiTaskPlatform import get_task_agent
        agent = await get_task_agent()
        result = await agent.diagnose(task_id=body.task_id)
        elapsed = (time.perf_counter() - t_start) * 1000
        report_len = len(result.get("report_md", ""))
        logger.info(f"[diagnose] 完成: task_id={body.task_id}, elapsed={elapsed:.0f}ms, "
                    f"report_len={report_len}, confidence={result.get('confidence', 0):.0%}")
        return {"code": 0, "data": result}
    except Exception as e:
        elapsed = (time.perf_counter() - t_start) * 1000
        # logger.exception 自动打印完整 traceback（含异常类型与堆栈），便于定位根因
        logger.exception(f"[diagnose] 失败: task_id={body.task_id}, elapsed={elapsed:.0f}ms")
        return {"code": 1, "message": str(e)}


@task_agent_router.post("/discuss", summary="@U老师 讨论")
async def task_discuss(body: TaskDiscussRequest) -> dict:
    """@U老师 讨论回复（带讨论上下文，按需调日志子Agent）→ 写 task_comments"""
    import logging, time
    logger = logging.getLogger("TASK_AGENT")
    t_start = time.perf_counter()
    query_preview = (body.query or "")[:60]
    logger.info(f"[discuss] 入口: task_id={body.task_id}, query={query_preview}")
    try:
        from ai.agents.AiTaskPlatform import get_task_agent
        agent = await get_task_agent()
        result = await agent.discuss(
            task_id=body.task_id,
            query=body.query,
            context=body.context,
        )
        elapsed = (time.perf_counter() - t_start) * 1000
        reply_len = len(result.get("reply", ""))
        logger.info(f"[discuss] 完成: task_id={body.task_id}, elapsed={elapsed:.0f}ms, "
                    f"reply_len={reply_len}")
        return {"code": 0, "data": result}
    except Exception as e:
        elapsed = (time.perf_counter() - t_start) * 1000
        # logger.exception 自动打印完整 traceback（含异常类型与堆栈），便于定位根因
        logger.exception(f"[discuss] 失败: task_id={body.task_id}, elapsed={elapsed:.0f}ms, "
                         f"query={query_preview}")
        return {"code": 1, "message": str(e)}


@task_agent_router.post("/summarize", summary="讨论摘要")
async def task_summarize(body: SummarizeRequest = SummarizeRequest()) -> dict:
    """后端触发 → U老师 自动扫描所有活跃工单 → 逐条生成摘要 → 写 task_comments"""
    import logging, time as _t
    logger = logging.getLogger("TASK_AGENT")
    t_start = _t.perf_counter()
    try:
        from ai.agents.AiTaskPlatform import get_task_agent
        agent = await get_task_agent()
        result = await agent.summarize_batch()
        elapsed = (_t.perf_counter() - t_start) * 1000
        logger.info(f"[summarize] 完成: elapsed={elapsed:.0f}ms, "
                    f"total={result.get('total')}, generated={result.get('generated')}, "
                    f"skipped={result.get('skipped')}, failed={result.get('failed')}")
        return {"code": 0, "data": result}
    except Exception as e:
        elapsed = (_t.perf_counter() - t_start) * 1000
        # logger.exception 自动打印完整 traceback（含异常类型与堆栈），便于定位根因
        logger.exception(f"[summarize] 失败: elapsed={elapsed:.0f}ms")
        return {"code": 1, "message": str(e)}


@task_agent_router.get("/health", summary="健康检查")
async def task_agent_health() -> dict:
    return {"status": "ok", "service": "ai-task-agent"}


class LogCacheCleanupRequest(BaseModel):
    task_id: str = Field(..., description="工单 ID")  # 该工单已解决/关闭，清理其日志缓存


@task_agent_router.post("/log-cache/cleanup", summary="清理工单日志缓存（已解决/已关闭时调用）")
async def task_log_cache_cleanup(body: LogCacheCleanupRequest) -> dict:
    """工单已解决/已关闭时，删除该工单的所有日志附件缓存（磁盘目录 + 内存索引）。

    由后端在 `update_ticket_status` 状态变更为 resolved/closed 时调用。
    """
    import logging
    logger = logging.getLogger("TASK_AGENT")
    try:
        from ai.core.log_cache import cleanup_task_log_cache
        removed = cleanup_task_log_cache(body.task_id)
        logger.info(f"[log-cache/cleanup] task_id={body.task_id}, removed_dirs={removed}")
        return {"code": 0, "data": {"task_id": body.task_id, "removed_dirs": removed}}
    except Exception as e:
        logger.exception(f"[log-cache/cleanup] 失败 task_id={body.task_id}: {e}")
        return {"code": 1, "message": str(e)}


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
