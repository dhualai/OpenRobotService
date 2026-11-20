"""工单关联策略配置 Service——读/写 system_config 表 + Redis 缓存热读。

默认策略：
  - 前置工单(PREDECESSOR) 阻塞 resolved + closed    → 默认开启
  - 子工单(SUBTASK)       不阻塞任何状态流转          → 默认关闭（弱关联）
  - 重复工单(DUPLICATE)   状态同步                    → 默认关闭

配置变更会主动删除 Redis 缓存，下一次读走 DB 重建缓存。
Redis 不可用时自动降级为纯 DB 模式，不影响主流程。
"""
import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.task import SystemConfig

logger = logging.getLogger(__name__)

# ── Redis 缓存 key ──
REDIS_KEY = "task_policy_config"
REDIS_TTL = 60  # 秒

# ── 默认策略 ──
DEFAULT_POLICIES: Dict[str, Dict[str, Any]] = {
    "block_predecessor_on_resolved": {
        "value": True,
        "description": "前置工单未完成时，禁止当前工单进入 resolved",
    },
    "block_predecessor_on_closed": {
        "value": True,
        "description": "前置工单未完成时，禁止当前工单进入 closed",
    },
    "block_subtask_on_resolved": {
        "value": False,
        "description": "子工单未完成时，禁止父工单进入 resolved（默认关闭：子工单仅弱关联）",
    },
    "block_subtask_on_closed": {
        "value": False,
        "description": "子工单未完成时，禁止父工单进入 closed（默认关闭：子工单仅弱关联）",
    },
    "duplicate_status_sync_enabled": {
        "value": False,
        "description": "重复工单(DUPLICATE)状态同步：一方状态变更自动同步给所有重复方（默认关闭）",
    },
}


def _bool_to_str(v: bool) -> str:
    return "1" if v else "0"


def _str_to_bool(s: str) -> bool:
    return str(s).strip() in ("1", "true", "True", "YES", "yes")


async def _get_redis():
    """获取异步 Redis 客户端（延迟导入，Redis 可选）。"""
    try:
        import redis.asyncio as aioredis
        url = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
        return aioredis.from_url(url, decode_responses=True)
    except Exception as e:
        logger.debug("task_policy Redis 不可用，降级为 DB 模式: %s", e)
        return None


async def _clear_redis():
    r = await _get_redis()
    if not r:
        return
    try:
        await r.delete(REDIS_KEY)
    except Exception as e:
        logger.debug("task_policy Redis delete 失败（可忽略）: %s", e)


async def _cache_read() -> Optional[Dict[str, Any]]:
    """从 Redis 读取缓存；未命中或不可用时返回 None。"""
    r = await _get_redis()
    if not r:
        return None
    try:
        raw = await r.get(REDIS_KEY)
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.debug("task_policy Redis get 失败: %s", e)
    return None


async def _cache_write(data: Dict[str, Any]):
    r = await _get_redis()
    if not r:
        return
    try:
        await r.setex(REDIS_KEY, REDIS_TTL, json.dumps(data))
    except Exception as e:
        logger.debug("task_policy Redis set 失败（可忽略）: %s", e)


async def _ensure_defaults(db: AsyncSession) -> None:
    """确保所有默认 key 都存在于 system_config 表。

    已存在的 key 不会被覆盖。只做 INSERT，不做 UPDATE。
    """
    for key, meta in DEFAULT_POLICIES.items():
        existing = (await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == key)
        )).scalar_one_or_none()
        if existing:
            continue
        db.add(SystemConfig(
            config_key=key,
            config_value=_bool_to_str(meta["value"]),
            description=meta["description"],
        ))
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def get_all_policies(db: AsyncSession, use_cache: bool = True) -> Dict[str, Any]:
    """获取所有工单策略配置（dict 形式，bool 值已自动转换）。

    优先走 Redis 缓存（use_cache=True），未命中或不可用时回查 DB。
    返回形如 {"block_predecessor_on_resolved": True, ...}。
    """
    if use_cache:
        cached = await _cache_read()
        if cached is not None:
            return cached

    await _ensure_defaults(db)

    rows = (await db.execute(select(SystemConfig))).scalars().all()
    result: Dict[str, Any] = {}
    for row in rows:
        if row.config_key in DEFAULT_POLICIES:
            result[row.config_key] = _str_to_bool(row.config_value)
        else:
            # 非 bool 配置（预留扩展）
            result[row.config_key] = row.config_value

    # 确保返回包含所有默认 key（即使 DB 里刚好被删了）
    for key, meta in DEFAULT_POLICIES.items():
        if key not in result:
            result[key] = meta["value"]

    await _cache_write(result)
    return result


async def get_policy(db: AsyncSession, key: str) -> Any:
    """读取单个策略值（便捷方法）。"""
    all_ = await get_all_policies(db)
    return all_.get(key, DEFAULT_POLICIES.get(key, {}).get("value"))


async def update_policies(
    db: AsyncSession,
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """批量更新策略配置。

    updates: {"block_predecessor_on_resolved": True, "duplicate_status_sync_enabled": False}
    只允许更新 DEFAULT_POLICIES 中定义的 key，拒绝任意 key 写入。

    返回更新后的完整策略字典。
    """
    allowed_keys = set(DEFAULT_POLICIES.keys())
    for k in updates.keys():
        if k not in allowed_keys:
            raise ValueError(f"未知的策略配置键: {k}")

    await _ensure_defaults(db)

    for key, value in updates.items():
        str_val = _bool_to_str(bool(value))
        row = (await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == key)
        )).scalar_one_or_none()
        if row:
            row.config_value = str_val
        else:
            db.add(SystemConfig(
                config_key=key,
                config_value=str_val,
                description=DEFAULT_POLICIES[key]["description"],
            ))

    await db.commit()
    await _clear_redis()

    return await get_all_policies(db, use_cache=False)


def get_policy_metadata() -> Dict[str, Dict[str, Any]]:
    """返回策略元数据（key → {default, description}），供前端渲染开关文案。"""
    return {k: {"default": v["value"], "description": v["description"]} for k, v in DEFAULT_POLICIES.items()}
