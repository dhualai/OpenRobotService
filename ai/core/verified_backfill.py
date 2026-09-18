"""P2 — 解决方案验证状态回填（verified backfill）—— core 共享层

从任务 Agent 平台仓库 `ai/agents/AiTaskPlatform/services/verified_backfill.py` 下沉合并到 `ai/core`，
作为方案沉淀验证闭环的共享基建（任务 Agent 方案沉淀 + 未来派单纠错信号都可复用）。

职责：把 P1 落库的 `verified=unknown` 升级为 confirmed/rejected/recurred，
让历史方案检索带着"是否经验证/被推翻"的可信度上阵。

信号判定（服务端确定性优先 + 可选 LLM 兜底）：
  - confirmed : 结案后讨论出现"解决了/好了/回退后正常/已验证/搞定"
  - rejected  : 结案后讨论出现"还是不行/没解决/无效/依旧/又报"
  - recurred  : 讨论出现"复发/又出现/再次出现/又报了"

原则：
  - 只对已结案（RESOLVED/CLOSED）工单回填，避免误判排查中的工单。
  - 只更新不删除；找不到对应向量点则跳过（不阻断）。
  - 每条工单只回填一次（扫到即更新，幂等）。

解耦：本版已归一使用 `app.models.task` + `app.core.database`，无平台依赖，可被所有 Agent 复用。
"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.core.retrieval import get_retrieval_service

logger = get_logger("VERIFIED_BACKFILL")

# 信号关键词（确定性优先）
_SIGNAL = {
    # (确认, 推翻) 两组关键词；命中确认组增分、命中推翻组减分
    "confirm": ["解决了", "好了", "恢复正常", "回退后正常", "已验证", "搞定", "有效", "没问题了", "修复了", "可以了"],
    "reject": ["还是不行", "没解决", "无效", "没有用", "依旧", "还报", "还是这样", "不行", "没反应", "没生效"],
    "recur": ["复发", "又出现", "又报了", "再次出现", "复现了", "又坏了", "又不行"],
}


def _detect_signal(comments_text: str) -> tuple[str, float]:
    """根据结案后讨论文本判定验证信号。

    Returns:
        (verdict, confidence)
          verdict: confirmed|rejected|recurred|unknown
          confidence: 0~1 粗糙置信度
    """
    text = (comments_text or "").lower()
    if not text.strip():
        return "unknown", 0.0

    score = 0
    for kw in _SIGNAL["confirm"]:
        if kw in text:
            score += 1
    for kw in _SIGNAL["reject"]:
        if kw in text:
            score -= 1
    for kw in _SIGNAL["recur"]:
        if kw in text:
            score += 2  # 复发信号强

    if score >= 2:
        # 复发优先（复现=新问题）
        if any(kw in text for kw in _SIGNAL["recur"]):
            return "recurred", 0.8
        return "confirmed", 0.7
    if score <= -1:
        return "rejected", 0.7
    if score == 1:
        return "confirmed", 0.5
    return "unknown", 0.0


async def backfill_verified_batch(batch_size: int = 50) -> dict:
    """扫描已结案工单 → 结案后讨论 → 回填 verified（服务端确定性判定）。

    Returns:
        {"scanned", "updated", "skipped", "failed"}
    """
    from app.models.task import Task, TaskStatus, TaskComment
    from app.core.db import SessionLocal

    retriever = await get_retrieval_service()

    db = SessionLocal()
    tasks = []
    try:
        tasks = db.query(Task).filter(
            Task.status.in_([TaskStatus.RESOLVED, TaskStatus.CLOSED])
        ).order_by(Task.resolved_at.desc()).limit(batch_size).all()
    except Exception as e:
        logger.error(f"[verified] 查询已结案工单失败: {e}")
        db.close()
        return {"scanned": 0, "updated": 0, "skipped": 0, "failed": 1, "error": str(e)[:100]}

    scanned = 0
    updated = 0
    skipped = 0
    failed = 0

    try:
        for task in tasks:
            scanned += 1
            tid = str(task.id)
            try:
                # 读结案后的讨论（resolved_at 之后）
                comments = db.query(TaskComment).filter(
                    TaskComment.task_id == task.id,
                ).order_by(TaskComment.created_at.asc()).all()
                resolved_at = getattr(task, "resolved_at", None) or getattr(task, "closed_at", None)
                follow = []
                for c in comments:
                    if resolved_at and c.created_at and c.created_at <= resolved_at:
                        continue
                    if (c.content or "").strip() and getattr(c, "created_by", "") != "小U":
                        follow.append(c.content)
                if not follow:
                    skipped += 1
                    continue  # 结案后无新讨论 → 无法判定，跳过

                verdict, _conf = _detect_signal("\n".join(follow))
                if verdict == "unknown":
                    skipped += 1
                    continue

                ok = await retriever.update_task_resolution_verified(tid, verdict)
                updated += 1 if ok else 0
                if ok:
                    logger.info(f"[verified] #{tid} → {verdict}")
                else:
                    skipped += 1  # 无向量点或更新失败，不重复计入失败
            except Exception as e:
                failed += 1
                logger.warning(f"[verified] #{tid} 回填异常: {e}")
    finally:
        db.close()

    logger.info(f"[verified] backfill done: scanned={scanned} updated={updated} skipped={skipped} failed={failed}")
    return {
        "scanned": scanned,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
    }
