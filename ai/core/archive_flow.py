# -*- coding: utf-8 -*-
"""归档流 AI 环节（ORS-986）：归档报告初稿流式生成 + 审核通过后知识自动入库。

backend 归档流（closed → 处理人开始归档 → 写报告 → 提交审核 → approved→archived）
中 AI 承担两步，backend 与前端之外零新增接口约定：

① 初稿生成（generate_stream_task）
   处理人点「开始归档」后前端调 /api/ai/archive/generate/stream，本模块后台
   任务组装素材（工单标题/描述/故障码/车型 + resolution_summary + 人类评论
   聚合，AI 评论剔除）→ LLM 流式生成归档报告 markdown（问题背景/现象/解决
   步骤/关联信息）→ 全文条件写回 archive_reports.content。
   竞态三层防护：
   - SQL 条件写回 WHERE content=''：处理人已手写则 AI 弃用，绝不覆盖；
   - SSE 是直播画面，生成与连接解耦——页面关掉任务照常完成落库；
   - 半截内容不进库，只有 done 时的完整全文一次写回。

② 知识入库（run_archive_ingest_worker）
   轮询 submit_status='approved' 且未打标的归档报告 → 报告全文作为知识源
   走 process_ticket（distiller 八条闸门照旧：测试单/无解法/敷衍跳过）→
   入库 company/ticket_resolutions（归档人审已前置，直接 approved 可检索）
   → 打 archive_knowledge_indexed 标记；失败不打标下轮重试。
"""
import asyncio
import json
import time

from ai.core.logging import get_logger

logger = get_logger("ARCHIVE_FLOW")

_POLL_INTERVAL = 60        # 入库 worker 轮询间隔（秒）
_GENERATE_TIMEOUT = 180    # 初稿生成整体超时（秒）

# 进行中的生成任务（task_id -> asyncio.Task）：幂等去重，同单不重复烧 LLM
_ACTIVE: dict[int, asyncio.Task] = {}

_SYSTEM_PROMPT = (
    "你是仓储物流机器人的资深售后工程师，为工单系统撰写归档报告。"
    "报告将存档并作为后续 AI 客服的知识来源。只依据给出的素材撰写，"
    "禁止编造素材之外的细节；素材没有覆盖的小节如实写「现场素材未提供，"
    "请处理人补充」。语言简洁、步骤落到可操作动作，保留故障码、车型、"
    "参数名等关键实体。"
)

_REPORT_PROMPT = """请根据以下工单素材撰写归档报告，格式要求：

第一行：`# 一句话标题（含现象与关键对象）`

然后依次四个小节，用 `## ` 二级标题：

## 问题背景
（谁、在什么场景/项目、做了什么操作后出现问题）

## 现象
（用户可见的具体异常表现，含故障码/日志关键字/界面提示）

## 解决步骤
（按顺序的可操作动作；素材中有多条线索时归纳成清晰的步骤序列）

## 关联信息
（涉及车型、部件、参数名、相关系统、后续注意事项）

----------

工单素材：

- 工单标题：{title}
- 工单描述：{description}
- 处理人解决摘要：{resolution_summary}
- 工单类型：{task_type}
- 项目：{project_name}
- 现场处理记录（评论，按时间）：
{comments}
"""


async def load_archive_context(task_id: int) -> dict | None:
    """查工单 + 最新归档报告行。返回 dict 或 None（工单/报告不存在）。"""
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        trow = db.execute(text(
            "SELECT t.id, t.title, t.description, t.metadata_info, t.task_type, "
            "       t.project_name, t.status, t.assigned_to "
            "FROM tasks t WHERE t.id = :tid"), {"tid": task_id}).mappings().first()
        if not trow:
            return None
        rrow = db.execute(text(
            "SELECT r.id, r.version, r.content, r.submit_status "
            "FROM archive_reports r WHERE r.task_id = :tid "
            "ORDER BY r.version DESC LIMIT 1"), {"tid": task_id}).mappings().first()
        if not rrow:
            return None
        ctx = dict(trow)
        ctx["report_id"] = rrow["id"]
        ctx["version"] = rrow["version"]
        ctx["content"] = rrow["content"] or ""
        ctx["submit_status"] = rrow["submit_status"]
        meta = ctx.get("metadata_info")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta) if meta else {}
            except Exception:
                meta = {}
        ctx["meta"] = meta if isinstance(meta, dict) else {}
        ctx["resolution_summary"] = ctx["meta"].get("resolution_summary") or ""
        ctx["comments"], ctx["last_human"] = (
            __import__("ai.core.solution_sink", fromlist=["load_comments_text"])
            .load_comments_text(task_id))
        return ctx
    finally:
        db.close()


def _writeback_if_empty(report_id: int, content: str) -> bool:
    """条件写回：仅当报告正文仍为空时写入（处理人已手写则弃用 AI 初稿）。

    单条 SQL 原子操作，返回是否写入。"""
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        res = db.execute(text(
            "UPDATE archive_reports SET content = :c, updated_at = NOW() "
            "WHERE id = :rid AND content = ''"), {"c": content, "rid": report_id})
        db.commit()
        return (res.rowcount or 0) > 0
    finally:
        db.close()


async def generate_stream_task(task_id: int, queue: asyncio.Queue, username: str) -> None:
    """后台生成任务：素材 → LLM 流式 → 全文条件写回。事件经 queue 推给 SSE。

    事件：status{msg} / title{text} / delta{text} / done{report_id,version,chars}
         / error{msg} / skipped{msg}（处理人已手写，初稿弃用）。"""
    from ai.core.database import SessionLocal
    from sqlalchemy import text

    async def q(ev: dict) -> None:
        await queue.put(ev)

    try:
        ctx = await load_archive_context(task_id)
        if ctx is None:
            await q({"type": "error", "msg": "工单或归档报告不存在"})
            return
        if ctx["submit_status"] not in ("draft", "rejected") or ctx["content"]:
            await q({"type": "skipped",
                     "msg": "报告已有内容（处理人已填写），AI 初稿不覆盖"})
            return

        await q({"type": "status", "msg": "正在收集工单信息与处理记录…"})
        prompt = _REPORT_PROMPT.format(
            title=ctx.get("title") or "", description=(ctx.get("description") or "")[:1500],
            resolution_summary=(ctx.get("resolution_summary") or "")[:800],
            task_type=ctx.get("task_type") or "", project_name=ctx.get("project_name") or "",
            comments=ctx.get("comments") or "（无评论记录）")

        from ai.core.solution_distiller import _get_distill_llm
        llm = await _get_distill_llm()

        await q({"type": "status", "msg": "正在总结问题与解决过程…"})
        buf: list[str] = []
        title_sent = False
        t0 = time.monotonic()
        async for token in llm.stream(prompt, system_prompt=_SYSTEM_PROMPT,
                                      max_tokens=4000, temperature=0.2, thinking=False):
            buf.append(token)
            full = "".join(buf)
            if not title_sent:
                # 首行 `# 标题` 完整（出现换行）后单独发 title 事件
                nl = full.find("\n")
                if nl >= 0:
                    first = full[:nl].lstrip("# ").strip()
                    if first:
                        await q({"type": "title", "text": first})
                    title_sent = True
            await q({"type": "delta", "text": token})
            if time.monotonic() - t0 > _GENERATE_TIMEOUT:
                await q({"type": "error", "msg": "生成超时，可直接手写报告"})
                return
        report = "".join(buf).strip()
        if not report:
            await q({"type": "error", "msg": "生成结果为空，可直接手写报告"})
            return

        await q({"type": "status", "msg": "正在保存初稿…"})
        # 写回前再查一次：生成期间处理人可能已开始手写
        ok = _writeback_if_empty(ctx["report_id"], report)
        if not ok:
            await q({"type": "skipped",
                     "msg": "处理人已自行填写，AI 初稿不覆盖（已弃用）"})
            return
        logger.info(f"[archive] #{task_id} 初稿已写入（{len(report)} 字，by {username}）")
        await q({"type": "done", "report_id": ctx["report_id"],
                 "version": ctx["version"], "chars": len(report)})
    except Exception as e:
        logger.exception(f"[archive] #{task_id} 初稿生成失败")
        await q({"type": "error", "msg": f"生成失败：{type(e).__name__}，可直接手写报告"})


def spawn_generation(task_id: int, queue: asyncio.Queue, username: str) -> bool:
    """受理生成请求：事件写入 queue。返回 False=同单已在生成中（幂等拒绝）。

    后台任务独立于 SSE 连接——连接断开任务照常完成落库。"""
    t = _ACTIVE.get(task_id)
    if t and not t.done():
        return False

    def _cleanup(_):
        _ACTIVE.pop(task_id, None)
    task = asyncio.create_task(generate_stream_task(task_id, queue, username))
    task.add_done_callback(_cleanup)
    _ACTIVE[task_id] = task
    return True


async def run_archive_ingest_worker(stop_event: asyncio.Event):
    """入库 worker：approved 归档报告 → 提炼知识卡 → 入库 → 打标。

    人审（归档报告 approve）已前置，通过闸门的卡直接 approved 可检索；
    失败不打标下轮重试；闸门跳过（empty）也打标防重复扫描。
    """
    from ai.core import get_retrieval_service, solution_sink

    logger.info("Archive ingest worker started (poll 60s)")
    retriever = None
    while not stop_event.is_set():
        try:
            from ai.core.database import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                rows = db.execute(text(
                    "SELECT r.task_id, r.task_id AS id, r.content, r.version, "
                    "       t.title, t.description, t.assigned_to, t.metadata_info, "
                    "       t.project_name, t.task_type "
                    "FROM archive_reports r JOIN tasks t ON t.id = r.task_id "
                    "WHERE r.submit_status = 'approved' AND r.content <> '' "
                    "  AND t.status = 'archived' "
                    "  AND JSON_EXTRACT(t.metadata_info, "
                    "      '$.archive_knowledge_indexed') IS NOT TRUE "
                    "  AND r.version = (SELECT MAX(r2.version) FROM archive_reports r2 "
                    "                   WHERE r2.task_id = r.task_id "
                    "                     AND r2.submit_status = 'approved') "
                    "ORDER BY r.task_id DESC LIMIT 10"
                )).mappings().all()
            finally:
                db.close()
            if rows:
                logger.info(f"[archive] 待入库归档报告 {len(rows)} 篇")
                if retriever is None:
                    retriever = await get_retrieval_service()
            for row in rows:
                if stop_event.is_set():
                    break
                tid = row["task_id"]
                try:
                    status = await solution_sink.process_ticket(
                        dict(row), retriever, archive_content=row["content"])
                    # process_ticket 已写 solution_indexed 系标记；补归档专属标记
                    # （failed 时不写——process_ticket failed 路径不打标，此处同样跳过）
                    if status in ("indexed", "empty"):
                        from ai.core.database import SessionLocal
                        from sqlalchemy import text
                        db2 = SessionLocal()
                        try:
                            db2.execute(text(
                                "UPDATE tasks SET metadata_info = JSON_MERGE_PATCH("
                                "  COALESCE(metadata_info, JSON_OBJECT()), "
                                "  JSON_OBJECT('archive_knowledge_indexed', TRUE, "
                                "              'archive_knowledge_indexed_at', :at)) "
                                "WHERE id = :tid"),
                                {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tid": tid})
                            db2.commit()
                        finally:
                            db2.close()
                    logger.info(f"[archive] #{tid} v{row['version']}: {status}")
                except Exception as e:
                    logger.exception(f"[archive] #{tid} 入库失败（下轮重试）: {e}")
        except Exception:
            logger.exception("[archive] 入库轮询异常")
        try:  # 可中断睡眠：stop 时立即退出而非睡满间隔
            await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
