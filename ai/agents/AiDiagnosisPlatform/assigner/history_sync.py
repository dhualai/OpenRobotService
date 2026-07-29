"""历史工单同步服务：从后端 tasks 表拉取已解决的工单分配记录。

缓存策略：首次请求或缓存过期时全量同步，TTL 10 分钟。
数据来源：tasks 表 status IN (resolved, closed) 且 assigned_to 非空的记录。
"""

import time
from typing import Dict, List, Optional, Set

from ai.core.logging import get_logger

logger = get_logger(__name__)

_history_cache: Optional[List[dict]] = None
_history_ts: Optional[float] = None
_CACHE_TTL = 600


def _extract_keywords(text: str, keyword_dict: Dict[str, List[str]]) -> Set[str]:
    if not text:
        return set()
    text_lower = text.lower()
    matched: Set[str] = set()
    for keywords in keyword_dict.values():
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.add(kw)
    return matched


def _fetch_from_tasks_table(module_keywords: Dict[str, List[str]]) -> list[dict]:
    from app.core.db import SessionLocal
    from app.models.task import Task, TaskStatus

    db = SessionLocal()
    try:
        rows = (
            db.query(Task)
            .filter(
                Task.status.in_([TaskStatus.RESOLVED, TaskStatus.CLOSED]),
                Task.assigned_to.isnot(None),
                Task.assigned_to != "",
            )
            .order_by(Task.created_at.desc())
            .limit(500)
            .all()
        )
        records = []
        for t in rows:
            title = t.title or ""
            desc = t.description or ""
            combined = f"{title} {desc}"
            keywords = _extract_keywords(combined, module_keywords)
            records.append({
                "engineer_id": t.assigned_to,
                "title": title,
                "description": desc[:300],
                "task_type": getattr(t, "task_type", None) or "problem",
                "keywords": keywords,
            })
        return records
    finally:
        db.close()


def load_history_records(
    module_keywords: Dict[str, List[str]],
    force: bool = False,
) -> List[dict]:
    global _history_cache, _history_ts
    if _history_cache is not None and not force:
        if _history_ts and (time.time() - _history_ts) < _CACHE_TTL:
            return _history_cache

    t0 = time.perf_counter()
    _history_cache = _fetch_from_tasks_table(module_keywords)
    _history_ts = time.time()
    logger.info(f"[history_sync] 同步完成: {len(_history_cache)} 条, {(time.perf_counter() - t0) * 1000:.0f}ms")
    return _history_cache


def invalidate_cache() -> None:
    global _history_cache, _history_ts
    _history_cache = None
    _history_ts = None
