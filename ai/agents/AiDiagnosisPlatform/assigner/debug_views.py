"""开发者模式：簇快照 / 历史工单概览。不参与派单主路径。"""

from __future__ import annotations

from typing import Dict

from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
    cluster_snapshot_from_cache,
    rebuild_cluster_cache,
)
from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


def _name_by_id() -> Dict[str, str]:
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import load_engineers
        return {e.id: e.name for e in (load_engineers() or []) if e.id}
    except Exception as e:
        logger.warning(f"[debug] 读工程师姓名失败: {e}")
        return {}


def _cluster_params() -> dict:
    cfg = AssignerConfig()
    hc = cfg.history_recall or {}
    return {
        "cluster_merge": hc.get("cluster_merge"),
        "cluster_min_size": hc.get("cluster_min_size"),
        "cluster_assign": hc.get("cluster_assign"),
        "cluster_top_k": hc.get("cluster_top_k"),
        "sim_threshold": hc.get("sim_threshold"),
        "top_k": hc.get("top_k"),
        "cluster_window": 500,
    }


async def debug_overview() -> dict:
    names = _name_by_id()
    clusters = cluster_snapshot_from_cache(names)
    clusters["params"] = _cluster_params()
    history = await history_overview(names)
    return {"clusters": clusters, "history": history}


async def debug_rebuild_clusters() -> dict:
    await rebuild_cluster_cache()
    snap = cluster_snapshot_from_cache(_name_by_id())
    snap["params"] = _cluster_params()
    return snap


async def history_overview(name_by_id: Dict[str, str] | None = None) -> dict:
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import _load_history_tasks
    from ai.core import get_retrieval_service

    names = name_by_id if name_by_id is not None else _name_by_id()
    records = _load_history_tasks()
    stats = {"collection": "", "points": 0}
    try:
        retriever = await get_retrieval_service()
        stats = await retriever.dispatch_history_stats()
    except Exception as e:
        logger.warning(f"[debug] Qdrant 统计失败: {e}")

    tickets = []
    for r in records[:80]:
        eid = (r.get("engineer_id") or "").strip()
        tickets.append({
            "ticket_id": r.get("ticket_id") or "",
            "title": r.get("title") or "",
            "engineer_id": eid,
            "engineer_name": names.get(eid) or eid,
            "task_type": r.get("task_type") or "",
            "robot_type": r.get("robot_type") or "",
            "fault_code": r.get("fault_code") or "",
            "closed_at": r.get("closed_at") or "",
        })
    return {
        "mysql_total": len(records),
        "qdrant_collection": stats.get("collection") or "",
        "qdrant_points": int(stats.get("points") or 0),
        "tickets": tickets,
        "params": _cluster_params(),
    }


async def debug_reindex() -> dict:
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import run_indexer

    index = await run_indexer(dry_run=False)
    history = await history_overview()
    return {"index": index, "history": history}
