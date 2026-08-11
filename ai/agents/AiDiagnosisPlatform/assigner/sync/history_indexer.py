"""存量补索引脚本：把 tasks 表所有已关闭(closed)工单一次性索引进 Qdrant dispatch_history。

用法（部署/初始化时手动跑一次）：
    uv run python -m ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer

之后历史工单随工单闭环持续入库（见 ai/core/retrieval.py::index_dispatch_history），
本脚本只在首次搭建派单历史向量库时补齐存量数据。

数据来源：与 sync/history_sync.py 一致——只取 status=closed（提单人确认解决）。
"""
import asyncio
import time

from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


def _load_closed_tasks() -> list[dict]:
    """从 tasks 表拉取所有 closed 工单（含 engineer_id 及召回所需字段）。"""
    from app.core.db import SessionLocal
    from app.models.task import Task, TaskStatus
    from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import (
        _extract_modules, _norm_task_type,
    )

    cfg = AssignerConfig()
    keyword_dict = cfg.module_keywords or {}

    db = SessionLocal()
    try:
        rows = (
            db.query(Task)
            .filter(
                Task.status == TaskStatus.CLOSED,
                Task.assigned_to.isnot(None),
                Task.assigned_to != "",
            )
            .order_by(Task.created_at.desc())
            .all()
        )
        records = []
        for t in rows:
            title = t.title or ""
            desc = t.description or ""
            combined = f"{title} {desc}"
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
                "description": desc[:2000],
                "modules": _extract_modules(combined, keyword_dict),
                "task_type": _norm_task_type(getattr(t, "task_type", None)),
                "fault_code": meta.get("fault_code") or "",
                "robot_type": meta.get("robot_type") or "",
                "closed_at": getattr(t, "created_at", None),
            })
        return records
    finally:
        db.close()


async def run_indexer(dry_run: bool = False) -> dict:
    """执行补索引。

    Args:
        dry_run: 只统计不写入（预览模式）。

    Returns:
        {"total": 总工单数, "indexed": 成功索引数, "skipped": 跳过/失败数, "collection": 集合名}
    """
    from ai.core import get_retrieval_service

    records = _load_closed_tasks()
    total = len(records)
    logger.info(f"[history_indexer] 待索引进 closed 工单: {total} 条")

    retriever = await get_retrieval_service()
    # 确保集合存在
    col = await retriever.ensure_dispatch_history_collection()
    if not col:
        logger.error("[history_indexer] 无法创建/定位 dispatch 集合，终止")
        return {"total": total, "indexed": 0, "skipped": total, "collection": ""}

    if dry_run:
        logger.info(f"[history_indexer] dry-run: 目标集合 {col}, 将索引 {total} 条")
        return {"total": total, "indexed": 0, "skipped": 0, "collection": col}

    t0 = time.perf_counter()
    ok = 0
    failed = 0
    for r in records:
        try:
            success = await retriever.index_dispatch_history(**r)
            if success:
                ok += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning(f"[history_indexer] 索引失败 {r.get('title','')}: {e}")
            failed += 1

    logger.info(
        f"[history_indexer] 完成: 成功 {ok} / 失败 {failed} / 共 {total}, "
        f"耗时 {(time.perf_counter() - t0) * 1000:.0f}ms, 集合 {col}"
    )
    return {"total": total, "indexed": ok, "skipped": failed, "collection": col}


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    result = asyncio.run(run_indexer(dry_run=dry))
    print(f"补索引结果: {result}")
