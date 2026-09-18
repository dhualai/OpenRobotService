"""存量补索引脚本：把 tasks 表已解决/已关闭工单索引进 Qdrant dispatch_history。

用法（部署/初始化或口径变更后手动跑一次）：
    uv run python -m ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer

数据来源：与 B 路 history_sync 一致——status ∈ {resolved, closed} 且有处理人。
同一工单用 ticket_id 稳定覆盖，解了再关不会写成两条。
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, dotenv_values
from sqlalchemy import create_engine, text

from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

_ENGINE = None


def _get_database_url() -> str:
    """解析后端数据库连接串（backend/.env 优先，ai/.env 兜底）。

    ⚠️ 刻意不 import app.core.config / app.core.db：
    它们会触发 backend 顶层 app 包的 __init__（setup_logging），而 logging 配置里
    硬编码了 backend.app... handler 类，导致在 `python -m` 单独跑脚本时循环导入崩溃
    （AttributeError: cannot access submodule 'app' of module 'backend'）。
    这里自举读取 DATABASE_URL，绕过该链路。
    """
    _project = Path(__file__).resolve().parents[5]  # → 项目根
    backend_env = _project / "backend" / ".env"
    for env_file in (backend_env, _project / "ai" / ".env"):
        if env_file.exists():
            vals = dotenv_values(env_file)
            url = (vals or {}).get("DATABASE_URL")
            if url:
                return url
    # 兜底：已注入到 os.environ 的情况
    return os.environ.get("DATABASE_URL", "") or "mysql+pymysql://root:123456@127.0.0.1:3306/helpdesk"


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        url = _get_database_url()
        _ENGINE = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return _ENGINE


def _as_iso(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _parse_metadata(meta) -> dict:
    if isinstance(meta, dict):
        return meta
    try:
        return json.loads(meta) if meta else {}
    except Exception:
        return {}


def build_index_record(
    *,
    ticket_id,
    engineer_id: str,
    title: str,
    description: str,
    task_type,
    metadata=None,
    finished_at=None,
    keyword_dict: Optional[dict] = None,
) -> dict:
    """把一张已解决/已关闭工单收成 A 路 Qdrant 写入字段（稳定 ticket_id 覆盖）。"""
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import (
        _extract_modules, _norm_task_type,
    )

    if keyword_dict is None:
        from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
        keyword_dict = AssignerConfig().module_keywords or {}
    title = title or ""
    desc = description or ""
    meta = _parse_metadata(metadata)
    return {
        "ticket_id": ticket_id,
        "engineer_id": engineer_id,
        "title": title,
        "description": desc[:2000],
        "modules": _extract_modules(f"{title} {desc}", keyword_dict),
        "task_type": _norm_task_type(task_type),
        "fault_code": meta.get("fault_code") or "",
        "robot_type": meta.get("robot_type") or "",
        "closed_at": _as_iso(finished_at),
    }


def _load_history_tasks() -> list[dict]:
    """从 tasks 表拉取 resolved + closed 工单（含 engineer_id 及召回所需字段）。

    自举实现：直接连库 + 原生 SQL，不依赖 backend 的 ORM 模型（避免触发
    backend app 包初始化导致的循环导入崩溃，见 _get_database_url 注释）。
    """
    from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig

    keyword_dict = AssignerConfig().module_keywords or {}

    db = _get_engine().connect()
    try:
        rows = db.execute(text(
            "SELECT id, title, description, task_type, assigned_to, "
            "       metadata_info, created_at, resolved_at, closed_at "
            "FROM tasks "
            "WHERE status IN ('resolved', 'closed') "
            "  AND assigned_to IS NOT NULL AND assigned_to != '' "
            "ORDER BY created_at DESC"
        )).mappings().all()

        records = []
        for t in rows:
            finished = t["resolved_at"] or t["closed_at"] or t["created_at"]
            records.append(build_index_record(
                ticket_id=t["id"],
                engineer_id=t["assigned_to"],
                title=t["title"] or "",
                description=t["description"] or "",
                task_type=t["task_type"],
                metadata=t["metadata_info"],
                finished_at=finished,
                keyword_dict=keyword_dict,
            ))
        return records
    finally:
        db.close()


async def index_history_records(records: list[dict]) -> dict:
    """把若干条历史工单 upsert 进 dispatch 集合。同一 ticket_id 覆盖写，不双条。"""
    from ai.core import get_retrieval_service

    total = len(records)
    if total == 0:
        return {"total": 0, "indexed": 0, "skipped": 0, "collection": ""}

    retriever = await get_retrieval_service()
    col = await retriever.ensure_dispatch_history_collection()
    if not col:
        logger.error("[history_indexer] 无法创建/定位 dispatch 集合，终止")
        return {"total": total, "indexed": 0, "skipped": total, "collection": ""}

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
            logger.warning(f"[history_indexer] 索引失败 {r.get('title', '')}: {e}")
            failed += 1

    logger.info(
        f"[history_indexer] 完成: 成功 {ok} / 失败 {failed} / 共 {total}, "
        f"耗时 {(time.perf_counter() - t0) * 1000:.0f}ms, 集合 {col}"
    )
    return {"total": total, "indexed": ok, "skipped": failed, "collection": col}


async def run_indexer(dry_run: bool = False) -> dict:
    """全量补索引（部署/口径变更后手动跑；日常结单走 Worker 增量）。"""
    records = _load_history_tasks()
    total = len(records)
    logger.info(f"[history_indexer] 待索引进 resolved+closed 工单: {total} 条")

    if dry_run:
        from ai.core import get_retrieval_service
        retriever = await get_retrieval_service()
        col = await retriever.ensure_dispatch_history_collection()
        logger.info(f"[history_indexer] dry-run: 目标集合 {col}, 将索引 {total} 条")
        return {"total": total, "indexed": 0, "skipped": 0, "collection": col or ""}

    return await index_history_records(records)


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    result = asyncio.run(run_indexer(dry_run=dry))
    print(f"补索引结果: {result}")
