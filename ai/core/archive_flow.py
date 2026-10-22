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
   轮询 submit_status='approved' 且未打标的归档报告（队列见 solution_sink.
   load_archived_candidates）→ 处理内核 solution_sink.process_archived_ticket：
   已有卡的单**只改 payload、向量不动**（与人工审核路逐字一致），没有卡的单
   用同一个提炼器补建（distiller 八条闸门照旧：测试单/无解法/敷衍跳过）→
   入库 company/ticket_resolutions（归档人审已前置，直接 approved 可检索）
   → 打 archive_sink_status 标记；失败不打标下轮重试。
   （2026-10-16 收口：此前这里自己拼 SQL + 调 process_ticket + 打
   archive_knowledge_indexed，与 knowledge_worker 的归档分支是两套并行实现，
   现在统一走 solution_sink，只留这一条驱动。）
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

_REPORT_PROMPT = """请根据以下工单素材撰写归档报告。报告只有两个小节，用 `## ` 二级标题，各节都须有正文：

第一行：`# 一句话标题（含现象与关键对象）`

## 问题描述
问题及背景：现象是什么、在什么条件下/什么环节出现，涉及的关键对象、
车型、故障码或日志关键字；咨询/责任归属类写清谁在什么场景下问了什么。
背景与条件要总结进来，不要只罗列一句话。

## 解决方案
办法及原因：这个问题的**原因是什么**（已知则写，含推测要标注），**怎么
解决**——按顺序的可操作动作；咨询类本节标题改用 `## 回答`，内容为结论
与依据。没有证据的部分明确提示处理人补充，不编造。

（素材提供了"现场图片"markdown 时：按图片内容与描述判断它最能佐证哪一节，
把 markdown **原样嵌入**该节正文之后的独立一行——如报错截图配「问题描述」、
现场照片配「解决方案/回答」；路径与文件名一字不改、不得虚构图片；每张
全篇恰好出现一次）

----------

工单素材：

- 工单标题：{title}
- 工单描述：{description}
- 处理人解决摘要：{resolution_summary}
- 工单类型：{task_type}
- 项目：{project_name}
- 现场处理记录（评论，按时间）：
{comments}
- 现场图片（markdown 引用可原样嵌入报告，前端渲染时带登录态加载）：
{image_refs}
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
            "WHERE id = :rid AND content = '' AND submit_status IN ('draft', 'rejected')"), {"c": content, "rid": report_id})
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
        # ORS-1035：评论区图片 → 报告可嵌入的 markdown 引用（相对路径无凭据，
        # 前端 MarkdownRenderer/AuthImage 渲染时带登录态加载）
        from ai.core.solution_sink import load_comment_image_refs
        img_refs = load_comment_image_refs(task_id)
        image_refs_text = "\n".join(
            f"- {r['markdown']}" + (f"（描述：{r['desc']}）" if r["desc"] else "")
            for r in img_refs) or "（评论区无图片）"
        prompt = _REPORT_PROMPT.format(
            title=ctx.get("title") or "", description=(ctx.get("description") or "")[:1500],
            resolution_summary=(ctx.get("resolution_summary") or "")[:800],
            task_type=ctx.get("task_type") or "", project_name=ctx.get("project_name") or "",
            comments=ctx.get("comments") or "（无评论记录）",
            image_refs=image_refs_text)

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
        # ORS-1035 兜底：素材有现场图片但 LLM 没嵌任何一张（内联指引偶尔失守）
        # → 自动补「## 现场图片」小节，保证图片不因模型失守而整体丢失
        if img_refs and "attachments/download" not in report:
            fallback = "\n\n## 现场图片\n\n" + "\n".join(
                f"- {r['markdown']}" + (f"（{r['desc']}）" if r["desc"] else "")
                for r in img_refs)
            report = report.rstrip() + fallback
            await q({"type": "delta", "text": fallback})

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
    """入库 worker：approved 归档报告 → 放行 / 补建知识卡 → 打标。

    队列与处理统一走 ai.core.solution_sink（load_archived_candidates /
    process_archived_ticket），不再自己拼 SQL、不再调 process_ticket ——
    否则会和 knowledge_worker 的归档分支各跑一遍、各打一个标记。
    人审（归档报告 approve）已前置：**已有卡的单只改 payload、向量一个字不动**
    （与人工审核路逐字一致），没有卡的单才用同一提炼器补建；标记统一写
    archive_sink_status，失败不打标下轮重试，闸门跳过（empty）也打标防重复扫描。
    """
    from ai.core import get_retrieval_service, solution_sink

    logger.info("Archive ingest worker started (poll 60s)")
    retriever = None
    while not stop_event.is_set():
        try:
            rows = solution_sink.load_archived_candidates(limit=10)
            if rows:
                logger.info(f"[archive] 待入库归档报告 {len(rows)} 篇")
                if retriever is None:
                    retriever = await get_retrieval_service()
            for row in rows:
                if stop_event.is_set():
                    break
                tid = row["id"]
                try:
                    # 打标在 process_archived_ticket 里（archive_sink_status）
                    status = await solution_sink.process_archived_ticket(row, retriever)
                    logger.info(f"[archive] #{tid} v{row.get('report_version')}: {status}")
                except Exception as e:
                    logger.exception(f"[archive] #{tid} 入库失败（下轮重试）: {e}")
        except Exception:
            logger.exception("[archive] 入库轮询异常")
        try:  # 可中断睡眠：stop 时立即退出而非睡满间隔
            await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
