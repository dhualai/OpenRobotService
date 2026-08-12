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
)

logger = get_logger("TASK_AGENT")


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

            last_summary_at_str = meta.get("ai_summary_at", "")
            last_summary_text = meta.get("ai_summary", "") or ""
            last_summary_at = None
            if last_summary_at_str:
                try:
                    from datetime import datetime as dt
                    last_summary_at = dt.strptime(last_summary_at_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            new_comments = []
            for c in comments:
                if c.created_by == "U老师":
                    continue
                if last_summary_at and c.created_at and c.created_at <= last_summary_at:
                    continue
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
        for c in new_comments[-20:]:
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

        summary = await self._llm_client.complete(
            prompt=prompt, system_prompt=SUMMARIZE_SYSTEM_PROMPT,
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
