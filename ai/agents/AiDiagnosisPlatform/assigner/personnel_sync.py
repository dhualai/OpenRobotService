"""人员信息同步服务：从后端 users 表拉取派单人数据。

缓存策略：首次请求或缓存过期时全量同步，TTL 10 分钟。
用户标识使用 users.id（唯一且稳定），避免同名冲突。
"""

import time
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile
from ai.core.logging import get_logger

logger = get_logger(__name__)

_sync_cache: Optional[List[EngineerProfile]] = None
_sync_ts: Optional[float] = None
_CACHE_TTL = 600


def _fetch_from_users_table() -> list[dict]:
    """从后端 users 表查询可用于派单的用户（status='active'）。"""
    from app.core.db import SessionLocal
    from app.models.identity import UserDB

    db = SessionLocal()
    try:
        rows = db.query(UserDB).filter(UserDB.status == "active").all()
        results = []
        for u in rows:
            modules = getattr(u, "responsibility_modules", None)
            if isinstance(modules, str):
                import json
                try:
                    modules = json.loads(modules)
                except Exception:
                    modules = {}
            # 兼容旧格式：扁平列表 → {"其他": [...]}
            if isinstance(modules, list):
                modules = {"其他": modules}
            if not isinstance(modules, dict):
                modules = {}
            results.append({
                "id": u.id,
                "name": getattr(u, "name", None) or u.username,
                "department": getattr(u, "department", None),
                "responsibility_modules": modules or [],
                "job_level": getattr(u, "job_level", 1),
                "duty_text": getattr(u, "duty_text", None),
            })
        return results
    finally:
        db.close()


def _build_profiles(rows: list[dict]) -> List[EngineerProfile]:
    profiles = []
    for row in rows:
        profiles.append(EngineerProfile(
            id=row["id"],
            name=row["name"],
            department=row["department"],
            responsibility_modules=row["responsibility_modules"],
            job_level=row["job_level"],
            duty_text=row["duty_text"],
        ))
    return profiles


def load_engineers(reload: bool = False) -> List[EngineerProfile]:
    global _sync_cache, _sync_ts
    if _sync_cache is not None and not reload:
        if _sync_ts and (time.time() - _sync_ts) < _CACHE_TTL:
            return _sync_cache

    t0 = time.perf_counter()
    rows = _fetch_from_users_table()
    _sync_cache = _build_profiles(rows)
    _sync_ts = time.time()
    logger.info(f"[personnel_sync] 同步完成: {len(_sync_cache)} 人, {(time.perf_counter() - t0) * 1000:.0f}ms")
    return _sync_cache


def invalidate_cache() -> None:
    global _sync_cache, _sync_ts
    _sync_cache = None
    _sync_ts = None
