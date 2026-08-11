"""历史工单同步服务：从后端 tasks 表拉取已关闭的工单分配记录。

缓存策略：首次请求或缓存过期时全量同步，TTL 10 分钟。
数据来源：tasks 表 status = closed（提单人确认已解决）且 assigned_to 非空的记录。
注意：只取 closed，不取 resolved——resolved 仅代表工程师单方认为解决，
      closed 才是提单人确认问题真正解决，作为历史经验更可靠。
"""

import time
from typing import Dict, List, Optional, Set

from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

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


def _extract_modules(text: str, keyword_dict: Dict[str, List[str]]) -> List[str]:
    """从文本判定工单归属的模块（问题域标签）。

    命中某模块的任意关键词即计入。返回命中模块列表，供 B路（问题域聚人）和
    A路（相似工单）payload 使用。
    """
    if not text or not keyword_dict:
        return []
    text_lower = text.lower()
    mods = []
    for mod, kws in keyword_dict.items():
        for kw in kws:
            if kw and kw.lower() in text_lower:
                mods.append(mod)
                break
    return mods


def _norm_task_type(t) -> str:
    """task_type 规范化：SQLAlchemy enum 对象 → 字符串（如 TaskType.PROBLEM → 'problem'）。"""
    try:
        if hasattr(t, "value"):
            return str(t.value)
        s = str(t)
        # "TaskType.problem" → "problem"
        return s.rsplit(".", 1)[-1].strip().lower() or "problem"
    except Exception:
        return "problem"


def _fetch_from_tasks_table(module_keywords: Dict[str, List[str]]) -> list[dict]:
    from app.core.db import SessionLocal
    from app.models.task import Task, TaskStatus

    db = SessionLocal()
    try:
        rows = (
            db.query(Task)
            .filter(
                # 只取已关闭：closed 才是提单人确认问题真正解决，作为历史经验更可靠
                Task.status == TaskStatus.CLOSED,
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
            # 从 metadata_info 提取故障码/车型（Agent 诊断结果落库存于 JSON）
            meta = getattr(t, "metadata_info", None)
            if not isinstance(meta, dict):
                try:
                    import json
                    meta = json.loads(meta) if meta else {}
                except Exception:
                    meta = {}
            records.append({
                "engineer_id": t.assigned_to,
                "title": title,
                "description": desc[:300],
                "task_type": _norm_task_type(getattr(t, "task_type", None)),
                "keywords": keywords,
                # ── 召回增强字段 ──
                "modules": _extract_modules(combined, module_keywords),  # 问题域标签（B路用）
                "created_at": getattr(t, "created_at", None),            # 时间衰减用
                "fault_code": meta.get("fault_code") or "",              # 故障码强匹配用
                "robot_type": meta.get("robot_type") or "",              # 车型匹配用
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
