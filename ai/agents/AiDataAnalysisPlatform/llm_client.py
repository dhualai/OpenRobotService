"""AI 数据分析平台 · 统一 LLM 客户端

不再直连在线大模型（DeepSeek / Qwen / GLM / SiliconFlow 等），
改为通过 HTTP 调用 ``ai/api/router.py`` 暴露的统一对话接口：

    POST /api/ai/chat          非流式对话
    POST /api/ai/chat/stream   流式对话（SSE）

模型调用的实际凭据与提供商配置由 AI 服务侧（``ai.core.LLMClient``）统一管理，
本模块只需知道 AI 服务的 HTTP 地址（``AnalysisConfig.api_base_url``）。

对外接口签名（``chat`` / ``chat_stream`` / ``model_name`` 等）保持不变，
因此 ``analyzer`` 与 ``agent`` 无需改动。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator

import httpx

from .config import AnalysisConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """统一大模型客户端（HTTP 模式）。

    通过 HTTP 调用 AI 服务的 ``/api/ai/chat`` 接口完成对话，
    不再直接持有 OpenAI SDK 凭据。

    用法::

        config = AnalysisConfig.from_env()
        client = LLMClient(config)
        content, usage = await client.chat(system_prompt, user_prompt)

    Note:
        ``/api/ai/chat`` 会按 ``session_id`` 在 Redis 中读写对话历史。
        为避免历史记忆污染数据分析结果（每次分析应独立无状态），
        每次调用均生成唯一 ``session_id``。
    """

    def __init__(self, config: AnalysisConfig) -> None:
        self._config = config
        self._base_url = config.api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=config.settings.timeout)
        # 仅用于展示；实际模型由 AI 服务侧决定，HTTP 接口不返回模型名
        self._model = config.provider_config.model
        self._temperature = config.settings.temperature
        self._max_tokens = config.settings.max_tokens

    # ── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _new_session_id() -> str:
        """生成唯一会话 ID，避免 /api/ai/chat 的历史记忆污染分析结果。"""
        return f"analysis-{uuid.uuid4().hex}"

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict:
        return {
            "session_id": self._new_session_id(),
            "query": user_prompt,
            "system_prompt": system_prompt or "",
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
        }

    # ── 同步接口 ────────────────────────────────────────────

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, dict | None]:
        """发送对话请求，返回 (回复文本, usage 信息)。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户消息。
            temperature: 临时覆盖温度参数。
            max_tokens: 临时覆盖最大 token 数。

        Returns:
            (模型回复文本, token 使用量字典)

        Note:
            ``/api/ai/chat`` 不返回 token 使用量，故 usage 恒为 ``None``。
        """
        payload = self._build_payload(
            system_prompt, user_prompt, temperature, max_tokens
        )
        url = f"{self._base_url}/api/ai/chat"
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("调用 %s 失败: %s", url, e)
            raise RuntimeError(f"AI 服务调用失败: {e}") from e

        data = resp.json()
        if data.get("code") != 0:
            inner = data.get("data", {}) or {}
            err = inner.get("error") or data.get("message") or "未知错误"
            raise RuntimeError(f"AI 服务返回错误: {err}")

        answer = (data.get("data", {}) or {}).get("answer", "")
        logger.info("LLM 调用完成（HTTP）answer_len=%d", len(answer))
        return answer, None

    async def chat_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """流式对话，逐 chunk 返回文本片段。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户消息。
            temperature: 临时覆盖温度参数。
            max_tokens: 临时覆盖最大 token 数。

        Yields:
            每个 chunk 的文本内容。
        """
        payload = self._build_payload(
            system_prompt, user_prompt, temperature, max_tokens
        )
        url = f"{self._base_url}/api/ai/chat/stream"
        try:
            async with self._client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    text = self._parse_sse_line(line)
                    if text is not None:
                        yield text
        except httpx.HTTPError as e:
            logger.error("调用 %s 失败: %s", url, e)
            raise RuntimeError(f"AI 服务流式调用失败: {e}") from e

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
        """解析单行 SSE，返回应 yield 的文本；无需 yield 时返回 None。

        /api/ai/chat/stream 的事件格式：
            data: {"token": "..."}      → 返回 token 文本
            data: {"error": "..."}      → 抛 RuntimeError
            data: {"ms": ...}           → 忽略（first_token 计时）
            data: {"total_ms": ...}     → 忽略（done 结束）
        """
        if not line or not line.startswith("data:"):
            return None
        raw = line[len("data:"):].strip()
        if not raw or raw == "[DONE]":
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if "error" in obj:
            raise RuntimeError(f"AI 服务流式错误: {obj['error']}")
        if "token" in obj:
            return obj["token"]
        return None

    # ── 便捷方法 ────────────────────────────────────────────

    @property
    def model_name(self) -> str:
        """当前配置的模型名称（仅展示用，实际模型由 AI 服务侧决定）。"""
        return self._model

    @property
    def provider_name(self) -> str:
        """当前提供商名称。"""
        return self._config.provider.value
