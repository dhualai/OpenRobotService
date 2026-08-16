"""TicketRefCapability — @# 跨工单引用只读能力（Phase 3，观察/兜底）

在 `@#` 引用场景里，用户 query 的 `@#编号` 已在 discuss() 入口由
`format_referenced_tickets` **预加载注入**（Q3c=B 主路径），因此本能力不作为
Supervisor 主动派发的主路径（避免重复注入）。

本能力的职责是**确定性观察/兜底**：
  - 当 Supervisor 在分析过程中、或将来讨论回复流里需要"专项读取某个被引用工单上下文"
    （非 query 入口顶部的那次预加载）时，以能力调用形式读取并返回。
  - 幂等：与被引用工单 ID 绑定，可安全重复调用（返回同一份上下文）。

依赖注入：`ticket_id`（或 `task_id` / `ref_id`）从 kwargs / runtime_ctx 传入。
读取走 `contexts.load_referenced_task_context`（L2：基本信息+诊断+solution）+ 讨论评论。
"""

from __future__ import annotations

from typing import Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


class TicketRefCapability(BaseCapability):
    """读取被 @# 引用的另一个工单的上下文（基本信息 + 诊断摘要 + 解决方案 + 讨论评论）。

    仅作参考/兜底，供 Supervisor 在需要专项查看某个被引用工单时调用。
    输入: ticket_id(被引用工单编号)。输出: 该工单的 L2 上下文文本。
    """

    name = "ticket_ref"
    description = (
        "跨工单引用：读取另一个工单的被引用上下文（标题/描述/状态/车型/故障码/诊断摘要/解决方案/讨论评论）。"
        "适用于用户 @#编号 引用历史工单、或要求参考某个具体工单的处理经验时，专项拉取其上下文。"
        "输入: ticket_id(工单编号)。输出: 该工单的上下文文本。"
        "注意: 若 @# 引用已在讨论入口整体注入，通常无需再单独调用（避免重复）。"
    )
    tags = ["ticket_ref", "引用", "跨工单", "参照工单", "@#"]

    def is_available(self) -> bool:
        """始终可用：纯只读、不依赖外部服务是否在线（DB 读取为主）。"""
        return True

    async def run(self, **kwargs) -> CapabilityResult:
        runtime_ctx = kwargs.get("runtime_ctx") or {}
        ticket_id = (
            kwargs.get("ticket_id")
            or kwargs.get("task_id")
            or kwargs.get("ref_id")
            or runtime_ctx.get("ticket_id")
            or runtime_ctx.get("task_id")
            or ""
        )

        try:
            from ai.agents.AiTaskPlatform.contexts import (
                extract_referenced_task_ids,
                format_referenced_tickets,
                build_query as _build_query,
            )
            if ticket_id:
                # 形态一：明确指定工单编号（@# 确定性引用/大脑点名读某单）→ 直接读该工单
                ids = extract_referenced_task_ids(f"@#{ticket_id}") if not str(ticket_id).isdigit() else [str(ticket_id)]
                if not ids:
                    return CapabilityResult.failure(f"无法解析被引用工单编号: {ticket_id}")
                text = format_referenced_tickets(ids)
                if not text:
                    return CapabilityResult(text="（未能读取到被引用工单上下文）", meta={"count": 0}, ok=True)
                return CapabilityResult(
                    text=text,
                    meta={"ticket_id": ids[0], "count": 1},
                    ok=True,
                )

            # 形态二：无明确编号 → 大脑决策，按当前工单上下文自动检索相似已解决工单并拉取其完整上下文
            cur = runtime_ctx.get("current_task") or {}
            query_text = " ".join(filter(None, [
                cur.get("problem_summary") or cur.get("title") or "",
                cur.get("description") or "",
                cur.get("fault_code") or "",
                cur.get("robot_type") or "",
            ])).strip()
            if not query_text:
                return CapabilityResult.failure("ticket_ref 无 ticket_id 且当前工单无检索文本，无法检索相似工单")

            similar_ids = _search_similar_resolved_task_ids(query_text, limit=3)
            if not similar_ids:
                return CapabilityResult(text="（未检索到相关的历史已解决工单）", meta={"count": 0}, ok=True)
            text = format_referenced_tickets(similar_ids)
            if not text:
                return CapabilityResult(text="（未检索到相关的历史已解决工单上下文）", meta={"count": 0}, ok=True)
            return CapabilityResult(
                text=text,
                meta={"similar_task_ids": similar_ids, "count": len(similar_ids)},
                ok=True,
            )
        except Exception as e:
            logger.warning(f"TicketRefCapability 执行失败: {e}")
            return CapabilityResult.failure(f"读取被引用工单失败: {type(e).__name__}: {e}")


def _search_similar_resolved_task_ids(query_text: str, limit: int = 3) -> list:
    """按当前工单文本检索相似【已解决】工单 id（AI 侧直查 DB，逻辑对齐后端 /api/tasks/similar）。

    关键词：2 字及以上中文 / 2 位以上字母数字；去掉停用词；标题命中 +3 / 描述命中 +1 打分。
    跨项目、无权限过滤、只取已解决、排除自身（自身由调用方保证不在候选）。
    """
    import re as _re
    try:
        from app.models.task import Task, TaskStatus
        from app.core.database import SessionLocal
        from sqlalchemy import or_, select
    except Exception as e:
        logger.warning(f"[ticket_ref] 相似检索依赖不可用: {e}")
        return []

    kws = set(_re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", query_text))
    kws.discard("问题")
    kws.discard("解决")
    if not kws:
        return []

    try:
        db = SessionLocal()
        try:
            conditions = []
            for kw in kws:
                pat = f"%{kw}%"
                conditions.append(Task.title.ilike(pat))
                conditions.append(Task.description.ilike(pat))
            stmt = (
                select(Task)
                .where(Task.status == TaskStatus.RESOLVED)
                .where(or_(*conditions))
                .order_by(Task.created_at.desc())
                .limit(limit * 4)
            )
            rows = (db.execute(stmt)).scalars().all()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[ticket_ref] 相似检索查询失败: {e}")
        return []

    scored = []
    for t in rows:
        title = t.title or ""
        desc = t.description or ""
        score = 0
        for kw in kws:
            if kw in title:
                score += 3
            if kw in desc:
                score += 1
        if score > 0:
            scored.append((score, t.id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [str(tid) for _score, tid in scored[:limit]]
