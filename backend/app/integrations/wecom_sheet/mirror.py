"""镜像同步：企微智能表格 → external_record。

设计要点：
- **全量拉 + 哈希判变**：企微 `get_records` 不支持按时间增量过滤，所以每次全量；
  但落库前用 `values_hash` 比对，未变的行只刷 `pulled_at`，这样才能统计出真实的
  `unchanged`，而不是像旧 adapter 那样每条都当 update。
- **镜像键 = record_id**：业务列（如「项目编号」）会被人改名，不能当主键。
- **不删行**：表格里消失的行只计入 `missing`（留在库里可追溯），误删代价太高。
- **单源串行**：同一 source 的同步用 asyncio.Lock 互斥，避免 Airflow 重试与手动触发叠加。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.integrations.wecom_sheet.client import WecomSheetClient, WecomSheetClientError
from app.models.wecom_sheet import ExternalRecord, WecomSheetSource

logger = logging.getLogger(__name__)

# {source_key: Lock}：只保证同进程内串行；跨进程靠 Airflow 不并发触发。
_locks: Dict[str, asyncio.Lock] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def values_hash(values: Any) -> str:
    """values 规范化哈希：键序无关，非 JSON 类型退化为 str。"""
    blob = json.dumps(values or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def sync_source(
    db: AsyncSession,
    source: WecomSheetSource,
    client: Optional[WecomSheetClient] = None,
) -> Dict[str, Any]:
    """把一个数据源同步进镜像表。调用方负责 commit 之外的异常处理。"""
    client = client or WecomSheetClient()
    records: List[Dict[str, Any]] = await client.fetch_all(source.docid, source.sheet_id)
    now = _now()

    rows = (await db.execute(
        select(ExternalRecord).where(ExternalRecord.source_key == source.key)
    )).scalars().all()
    existing = {r.record_id: r for r in rows}

    seen: set[str] = set()
    created = updated = unchanged = skipped = 0

    for r in records:
        record_id = str(r.get("record_id") or "").strip()
        if not record_id:
            skipped += 1
            continue
        values = r.get("values") or {}
        digest = values_hash(values)
        seen.add(record_id)

        row = existing.get(record_id)
        if row is None:
            db.add(ExternalRecord(
                source_key=source.key,
                record_id=record_id,
                values=values,
                raw_hash=digest,
                pulled_at=now,
                changed_at=now,
                created_at=now,
            ))
            created += 1
            continue
        row.pulled_at = now
        if row.raw_hash != digest:
            row.values = values
            row.raw_hash = digest
            row.changed_at = now
            updated += 1
        else:
            unchanged += 1

    missing = sum(1 for rid in existing if rid not in seen)

    stats = {
        "fetched": len(records),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "missing": missing,
        "skipped": skipped,
        "total": len(existing) + created,
    }

    source.last_sync_at = now
    source.last_stats = stats
    source.last_error = None
    source.updated_at = now
    await db.commit()
    logger.info("企微表格 %s 同步完成: %s", source.key, stats)
    return stats


async def run_sync(source_key: str, client: Optional[WecomSheetClient] = None) -> Dict[str, Any]:
    """按 key 同步一次（自带会话 + 串行锁 + 错误落库）。"""
    lock = _lock_for(source_key)
    if lock.locked():
        return {"skipped": True, "reason": "该数据源正在同步中"}

    async with lock:
        async with AsyncSessionLocal() as db:
            source = (await db.execute(
                select(WecomSheetSource).where(WecomSheetSource.key == source_key)
            )).scalar_one_or_none()
            if source is None:
                raise KeyError(f"数据源不存在: {source_key}")
            if not source.enabled:
                return {"skipped": True, "reason": "数据源已停用"}

            source_id = source.id
            try:
                return await sync_source(db, source, client)
            except Exception as e:
                await db.rollback()
                detail = f"{type(e).__name__}: {e}"[:2000]
                row = await db.get(WecomSheetSource, source_id)
                if row is not None:
                    row.last_error = detail
                    row.last_sync_at = _now()
                    row.updated_at = _now()
                    await db.commit()
                logger.warning("企微表格 %s 同步失败: %s", source_key, detail)
                raise


async def run_sync_all(client: Optional[WecomSheetClient] = None) -> List[Dict[str, Any]]:
    """同步所有已启用数据源（供通用 DAG / 定时任务用）。

    单源失败不影响其他源：错误已由 run_sync 落库到 last_error。
    """
    async with AsyncSessionLocal() as db:
        sources = (await db.execute(
            select(WecomSheetSource).where(WecomSheetSource.enabled.is_(True))
        )).scalars().all()
        keys = [s.key for s in sources]

    results: List[Dict[str, Any]] = []
    for key in keys:
        try:
            stats = await run_sync(key, client)
            results.append({"key": key, "ok": True, **stats})
        except Exception as e:  # 单源失败不中断整批
            results.append({"key": key, "ok": False, "error": f"{type(e).__name__}: {e}"[:500]})
    return results
