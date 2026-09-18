"""backend 自维护的轻量 LLM 客户端。

只实现 backend 需要的功能：DeepSeek（OpenAI 兼容）非流式补全 + 重试。
不依赖 ai/core/llm.py，避免跨模块依赖泄漏（如 tenacity 缺失导致 503）。

密钥/模型取 backend/.env（settings.LLM_API_KEY / LLM_API_URL / LLM_MODEL_NAME），
与「文件导入 AI 识别」共用同一配置，默认 DeepSeek flash。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("admin")

# ── 超时与重试 ──────────────────────────────────────────────
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0          # 摘要/阻滞分析等补全类任务可能较长
MAX_RETRIES = 3
RETRY_DELAYS = [1, 4]         # 指数退避：第 1 次重试等 1s，第 2 次等 4s


class LLMError(RuntimeError):
    """LLM 调用异常（接口层映射为 503）。"""


def _build_payload(
    model: str,
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    thinking: bool = False,
) -> Dict[str, Any]:
    """构建 DeepSeek chat/completions 请求体。

    DeepSeek/miMo 思考模式默认开启，传 thinking=False 显式关闭（与 ai/core/llm.py 行为一致）。
    """
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if any(x in model.lower() for x in ("deepseek", "mimo")):
        if thinking is False:
            payload["thinking"] = {"type": "disabled"}
        else:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "low"
    return payload


def _extract_content(response: Dict[str, Any]) -> str:
    """从 DeepSeek/OpenAI 兼容响应中提取正文。"""
    msg = response["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning_content", "")


class LLMClient:
    """轻量异步 LLM 客户端（DeepSeek / OpenAI 兼容）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
    ):
        self.api_key = api_key
        # 兼容用户配置成完整 chat/completions URL 的情况：截到 base
        if "/chat/completions" in base_url:
            base_url = base_url.rsplit("/chat/completions", 1)[0]
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT,
                    read=READ_TIMEOUT,
                    write=10.0,
                    pool=10.0,
                ),
                trust_env=False,
                limits=httpx.Limits(
                    max_keepalive_connections=5, max_connections=10
                ),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _make_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """发送请求，处理 HTTP 错误码。"""
        client = await self._get_client()
        url = f"{self.base_url}/chat/completions"
        response = await client.post(url, json=payload)

        if response.status_code == 401:
            raise LLMError("API 密钥无效或已过期")
        elif response.status_code == 429:
            raise LLMError("请求频率超限，请稍后重试")
        elif response.status_code != 200:
            raise LLMError(
                f"API 返回错误码: {response.status_code}, "
                f"body: {response.text[:200]}"
            )
        try:
            return response.json()
        except Exception as e:
            raise LLMError(
                f"响应 JSON 解析失败({e})，"
                f"status={response.status_code}，body 前 200 字符: {response.text[:200]}"
            )

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.3,
        thinking: bool = False,
    ) -> str:
        """非流式补全（单轮对话）。网络类异常自动重试，其余直接抛 LLMError。"""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = _build_payload(
            self.model,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
        )

        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                data = await self._make_request(payload)
                content = _extract_content(data)
                if not content:
                    raise LLMError("大模型没有返回内容，请稍后重试")
                return content
            except LLMError:
                raise
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as e:
                last_exc = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
                    logger.warning(
                        "[llm] 请求失败(第 %d 次)，%ss 后重试: %s",
                        attempt + 1, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise LLMError(f"LLM 请求超时（重试耗尽）: {e}") from e
            except Exception as e:
                raise LLMError(f"LLM 请求失败: {e}") from e

        raise LLMError(f"LLM 请求失败（重试耗尽）: {last_exc}")


# ── 单例 ─────────────────────────────────────────────────────
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取 backend LLM 客户端单例。

    密钥/模型取 settings（backend/.env），与「文件导入 AI 识别」共用同一配置。
    未配置时抛 LLMError（接口层映射为 503）。
    """
    global _client
    if _client is not None:
        return _client
    if not settings.LLM_API_KEY:
        raise LLMError(
            "AI 服务未配置：请在 backend/.env 设置 LLM_API_KEY"
        )
    base_url = settings.LLM_API_URL or "https://api.deepseek.com/chat/completions"
    _client = LLMClient(
        api_key=settings.LLM_API_KEY,
        base_url=base_url,
        model=settings.LLM_MODEL_NAME,
    )
    return _client
