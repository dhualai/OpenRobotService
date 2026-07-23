"""
企业微信 access_token 管理

- 首次调用时请求 gettoken 接口
- 内存缓存，7200s 有效期，提前 300s 刷新
- 线程安全
"""
import time
import asyncio
import logging
from typing import Optional

import httpx

from ai.config import get_ai_config

logger = logging.getLogger("ai.wecom.token")

# 提前多久刷新（秒）
_REFRESH_BEFORE = 300


class AccessTokenManager:
    """企业微信 access_token 缓存管理器"""

    def __init__(self, corpid: str = "", corpsecret: str = ""):
        cfg = get_ai_config()
        self._corpid = corpid or cfg.wecom_corpid
        self._corpsecret = corpsecret or cfg.wecom_corpsecret
        self._token: Optional[str] = None
        self._expires_at: float = 0.0  # unix timestamp
        self._lock = asyncio.Lock()

    @property
    def _base_url(self) -> str:
        return "https://qyapi.weixin.qq.com"

    async def get_token(self) -> str:
        """获取有效 token（自动刷新）"""
        now = time.time()
        if self._token and now < self._expires_at - _REFRESH_BEFORE:
            return self._token

        async with self._lock:
            # 双重检查
            if self._token and time.time() < self._expires_at - _REFRESH_BEFORE:
                return self._token

            if not self._corpid or not self._corpsecret:
                raise ValueError("企业微信 corpid / corpsecret 未配置，请检查 .env")

            url = f"{self._base_url}/cgi-bin/gettoken"
            params = {"corpid": self._corpid, "corpsecret": self._corpsecret}

            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(url, params=params)
                data = resp.json()

            if data.get("errcode") != 0:
                raise RuntimeError(f"获取 access_token 失败: {data}")

            self._token = data["access_token"]
            self._expires_at = time.time() + data.get("expires_in", 7200)
            logger.info(f"access_token 已刷新, expires_in={data.get('expires_in')}s")
            return self._token

    def invalidate(self):
        """强制 token 失效，下次 get_token 重新获取"""
        self._token = None
        self._expires_at = 0.0
