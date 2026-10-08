"""从后端拉取 USP 环境的 SSH 配置（X-API-Key）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


async def fetch_usp_env_ssh_config(env_id: int) -> Optional[Dict[str, Any]]:
    """GET /api/usp-envs/{id}/ssh-config。失败返回 None（上层静默降级）。"""
    try:
        from ai.config import get_ai_config
        cfg = get_ai_config()
        base = (cfg.backend_base_url or "").rstrip("/")
        key = cfg.internal_api_key or ""
        if not base or not key:
            logger.warning("[usp_env] backend_base_url / internal_api_key 未配置")
            return None
        url = f"{base}/api/usp-envs/{int(env_id)}/ssh-config"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"X-API-Key": key})
            if resp.status_code != 200:
                logger.warning(
                    f"[usp_env] 取 SSH 配置失败 env={env_id} status={resp.status_code} body={resp.text[:200]}"
                )
                return None
            payload = resp.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning(f"[usp_env] 取 SSH 配置异常 env={env_id}: {e}")
        return None
