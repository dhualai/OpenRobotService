"""人员信息同步服务：从后端 users 表拉取派单人数据。

缓存策略：首次请求或缓存过期时全量同步，TTL 10 分钟。
用户标识使用 users.username（唯一且稳定，真实环境为 wechat_ 前缀），
与 tasks.created_by / assigned_to 保持一致，避免反复查表。
"""

import time
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

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
                "id": getattr(u, "username", None),  # 统一用 username 作为工程师标识（真实环境为 wechat_ 前缀）
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
    skipped = 0
    for row in rows:
        # ── 准入校验：三个必填字段 ──
        dept = (row.get("department") or "").strip()
        modules = row.get("responsibility_modules") or {}
        # responsibility_modules 不能是空 dict
        if isinstance(modules, dict):
            modules = {k: v for k, v in modules.items() if v}  # 去掉空列表的 key
            has_modules = any(modules.values())
        else:
            has_modules = bool(modules)

        if not dept:
            logger.debug(f"[engineers_sync] 跳过 {row.get('name')}: 缺少 department")
            skipped += 1
            continue
        if not has_modules:
            logger.debug(f"[engineers_sync] 跳过 {row.get('name')}: responsibility_modules 为空")
            skipped += 1
            continue

        profiles.append(EngineerProfile(
            id=row["id"],
            name=row["name"],
            department=dept,
            responsibility_modules=modules,
            job_level=row.get("job_level", 1),
            duty_text=row.get("duty_text"),  # 有更好，没有也行
        ))

    if skipped:
        logger.info(f"[engineers_sync] 准入校验: 跳过 {skipped} 人 (缺 department/modules)")
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
    logger.info(f"[engineers_sync] 同步完成: {len(_sync_cache)} 人, {(time.perf_counter() - t0) * 1000:.0f}ms")
    return _sync_cache


def invalidate_cache() -> None:
    global _sync_cache, _sync_ts
    _sync_cache = None
    _sync_ts = None
