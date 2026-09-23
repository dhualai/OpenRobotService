"""讨论摘要流程 — 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的 summarize_batch / _summarize_one（保持 self.xxx 调用不变，仅拆分文件）。
summarize = 后端触发，扫描活跃工单，逐条判断是否生成摘要，写 metadata_info.ai_summary。
"""

import time

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.prompts import (
    SUMMARIZE_SYSTEM_PROMPT,
    SUMMARIZE_FULL_TEMPLATE,
    SUMMARIZE_INCREMENTAL_TEMPLATE,
    select_system_prompt as _select_system_prompt,
)

logger = get_logger("TASK_AGENT")


def _task_is_platform(task, title: str, desc: str) -> bool:
    """判断任务是否属于摇人吧服务号（平台）场景，用于选 U老师 role。"""
    try:
        from ai.agents.AiTaskPlatform.schemas import TaskContext
        from ai.agents.AiTaskPlatform.contexts.contexts import is_platform_ticket
        t = task.title if (task is not None and getattr(task, "title", None)) else (title or "")
        d = task.description if (task is not None and getattr(task, "description", None)) else (desc or "")
        return is_platform_ticket(TaskContext(task_id="0", title=t or "", description=d or ""))
    except Exception:
        return False


class SummarizeFlow:
    _SUMMARY_MIN_NEW_COMMENTS = 2
    _SUMMARY_ACTIVE_STATUSES = ("new", "pending", "in_progress")

    # ============================================================
    # summarize — 讨论摘要（后端触发 → 扫描所有活跃工单 → 逐条判断生成）
    # ============================================================

    async def summarize_batch(self) -> dict:
        """后端触发入口：扫描所有活跃工单 → 逐条判断是否需生成摘要 → 写 task_comments"""
        from app.models.task import Task, TaskStatus
        from app.core.database import SessionLocal

        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        db = SessionLocal()
        try:
            tasks = db.query(Task).filter(
                Task.status == TaskStatus.IN_PROGRESS
            ).all()
        finally:
            db.close()

        results = []
        for task in tasks:
            try:
                r = await self._summarize_one(str(task.id))
                results.append(r)
            except Exception as e:
                logger.error(f"Summarize #{task.id} failed: {e}")
                results.append({"task_id": str(task.id), "error": str(e)[:100]})

        generated = sum(1 for r in results if not r.get("skipped") and not r.get("error"))
        skipped = sum(1 for r in results if r.get("skipped"))
        failed = sum(1 for r in results if r.get("error"))

        total_ms = round((time.perf_counter() - t0) * 1000)
        logger.info(f"Summarize batch done: {len(tasks)}工单, {generated}生成/{skipped}跳过/{failed}失败, {total_ms}ms")
        return {
            "total": len(tasks),
            "generated": generated,
            "skipped": skipped,
            "failed": failed,
            "items": results,
            "_total_ms": total_ms,
        }

    async def _summarize_one(self, task_id: str) -> dict:
        """单条工单摘要：读DB→判断→生成→写DB"""
        from app.models.task import Task, TaskComment
        from app.core.database import SessionLocal

        t0 = time.perf_counter()
        tid_int = int(task_id)

        db = SessionLocal()
        try:
            comments = db.query(TaskComment).filter(
                TaskComment.task_id == tid_int
            ).order_by(TaskComment.created_at.asc()).all()

            task = db.query(Task).filter(Task.id == tid_int).first()
            task_title = task.title if task else ""
            task_desc = task.description if task else ""
            meta = dict(task.metadata_info or {}) if task else {}
            diag = meta.get("diagnosis", {})
            diag_summary = diag.get("problem_summary", "") or ""

            # 取工单处理人/创建人作为"当前用户"注入画像（摘要按处理人身份调整侧重点）
            task_username = (task.assigned_to or task.created_by or "").strip() if task else ""

            last_summary_at_str = meta.get("ai_summary_at", "")
            last_summary_text = meta.get("ai_summary", "") or ""
            last_summary_at = None
            if last_summary_at_str:
                try:
                    from datetime import datetime as dt
                    last_summary_at = dt.strptime(last_summary_at_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            # 触发判断：只累计"非 U老师"的摘要后新评论（U老师的自答不算他人进展）
            new_comments = []
            # 摘要内容素材：摘要之后的全部评论（含 U老师，让 AI 的回复/诊断也参与提炼）
            summary_comments = []
            for c in comments:
                if last_summary_at and c.created_at and c.created_at <= last_summary_at:
                    continue
                if c.content is not None and str(c.content or "").strip():
                    summary_comments.append(c)
                    if c.created_by != "U老师":
                        new_comments.append(c)
        finally:
            db.close()

        if len(new_comments) < self._SUMMARY_MIN_NEW_COMMENTS:
            return {
                "task_id": task_id,
                "skipped": True,
                "reason": f"新评论不足({len(new_comments)}/{self._SUMMARY_MIN_NEW_COMMENTS})",
                "new_comments": len(new_comments),
                "_total_ms": round((time.perf_counter() - t0) * 1000),
            }

        history_lines = []
        for c in summary_comments[-20:]:
            author = getattr(c, 'created_by_name', None) or c.created_by or "?"
            content = (c.content or "")[:200]
            history_lines.append(f"[{author}] {content}")
        history_text = "\n".join(history_lines) if history_lines else ""

        if last_summary_text:
            prompt = SUMMARIZE_INCREMENTAL_TEMPLATE.format(
                previous_summary=last_summary_text,
                discussion_history=history_text,
            )
        else:
            prompt = SUMMARIZE_FULL_TEMPLATE.format(
                title=task_title or f"工单 #{task_id}",
                description=task_desc or "",
                diagnosis_summary=diag_summary or "无",
                discussion_history=history_text,
            )

        # 用户画像注入（按工单处理人身份调整摘要侧重点）
        system_prompt = _select_system_prompt(_task_is_platform(task, task_title, task_desc), "summarize")
        if task_username:
            try:
                from ai.core.user_profile import resolve_user_profile, format_user_profile_block
                profile = await resolve_user_profile(task_username)
                profile_block = format_user_profile_block(profile)
                if profile_block:
                    system_prompt = f"{system_prompt}\n\n{profile_block}"
            except Exception as e:
                logger.warning(f"[summarize] 用户画像解析失败(降级无画像): task={task_id}, user={task_username}, err={e}")

        summary = await self._llm_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=300, temperature=0.3,
        )
        summary_text = summary.strip()

        # ── 存入 metadata_info.ai_summary（不写 task_comments）──
        from app.models.task import Task as _Task
        from app.core.database import SessionLocal as _SL
        db2 = _SL()
        try:
            task = db2.query(_Task).filter(_Task.id == tid_int).first()
            if task:
                meta = dict(task.metadata_info or {})
                meta["ai_summary"] = summary_text
                meta["ai_summary_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                task.metadata_info = meta
                db2.commit()
        finally:
            db2.close()

        logger.info(f"Summarize #{task_id}: 生成完成, new_comments={len(new_comments)}")
        return {
            "task_id": task_id,
            "summary": summary_text,
            "new_comments": len(new_comments),
            "skipped": False,
            "stored_in": "metadata_info.ai_summary",
            "_total_ms": round((time.perf_counter() - t0) * 1000),
        }
