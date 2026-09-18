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
import uuid
from typing import AsyncIterator

import httpx

from .config import AnalysisConfig
from .logging_config import get_logger

logger = get_logger("LLMClient")


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
        默认每次调用生成唯一 ``session_id``（数据分析应独立无状态）；
        对话类调用可显式传入稳定的 ``session_id`` 复用历史，实现多轮记忆。
    """

    def __init__(self, config: AnalysisConfig) -> None:
        self._config = config
        self._base_url = config.api_base_url.rstrip("/")
        # Python 3.14 + httpx 0.28.1 在 Windows 上回环连接(localhost→同一服务)
        # 默认传输层在 IPv6 回退时返回 502，强制 IPv4 传输解决
        _transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        self._client = httpx.AsyncClient(
            transport=_transport, timeout=config.settings.timeout
        )
        # 仅用于展示；实际模型由 AI 服务侧决定，HTTP 接口不返回模型名
        self._model = config.provider_config.model
        self._temperature = config.settings.temperature
        self._max_tokens = config.settings.max_tokens

    # ── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _new_session_id() -> str:
        """生成唯一会话 ID。

        未显式传入 ``session_id`` 时使用，保证无状态调用不污染分析结果。
        """
        return f"analysis-{uuid.uuid4().hex}"

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None,
        max_tokens: int | None,
        session_id: str | None = None,
    ) -> dict:
        return {
            # 显式传入时复用该会话（AI 服务端自动注入历史对话）→ 多轮记忆；
            # 否则每次唯一 ID → 无状态（数据分析默认行为）
            "session_id": session_id or self._new_session_id(),
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
        session_id: str | None = None,
    ) -> tuple[str, dict | None]:
        """发送对话请求，返回 (回复文本, usage 信息)。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户消息。
            temperature: 临时覆盖温度参数。
            max_tokens: 临时覆盖最大 token 数。
            session_id: 会话 ID；传入后 AI 服务端自动注入该会话历史
                （多轮记忆），不传则每次无状态调用。

        Returns:
            (模型回复文本, token 使用量字典)

        Note:
            ``/api/ai/chat`` 不返回 token 使用量，故 usage 恒为 ``None``。
        """
        payload = self._build_payload(
            system_prompt, user_prompt, temperature, max_tokens, session_id
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
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        """流式对话，逐 chunk 返回文本片段。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户消息。
            temperature: 临时覆盖温度参数。
            max_tokens: 临时覆盖最大 token 数。
            session_id: 会话 ID；传入后 AI 服务端自动注入该会话历史
                （多轮记忆），不传则每次无状态调用。

        Yields:
            每个 chunk 的文本内容。
        """
        payload = self._build_payload(
            system_prompt, user_prompt, temperature, max_tokens, session_id
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

    async def chat_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list,
        *,
        messages: list | None = None,
        session_id: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        save_memory: bool = True,
    ) -> dict:
        """带工具定义的对话调用（OpenAI tools 协议），返回 {content, tool_calls}。

        Args:
            system_prompt: 系统提示词（messages 模式时忽略）。
            user_prompt: 用户消息；messages 模式时仅用于会话记忆保存。
            tools: OpenAI tools 协议的工具定义列表。
            messages: 完整消息列表（agentic 多轮工具循环）；非空时优先。
            session_id: 会话 ID（多轮记忆）；不传则无状态调用。
            temperature / max_tokens: 临时覆盖。
            save_memory: 是否保存本轮问答到会话记忆；工具中间轮传 False。

        Returns:
            {"content": 回答文本, "tool_calls": [{id, name, arguments(dict)}]}
        """
        payload: dict = {
            "session_id": session_id or self._new_session_id(),
            "query": user_prompt,
            "system_prompt": system_prompt or "",
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
            "tools": tools,
            "messages": messages,
            "save_memory": save_memory,
        }
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

        inner = data.get("data", {}) or {}
        content = inner.get("answer", "")
        tool_calls = inner.get("tool_calls") or []
        logger.info(
            "LLM 工具调用完成（HTTP）content_len=%d tool_calls=%d",
            len(content), len(tool_calls),
        )
        return {"content": content, "tool_calls": tool_calls}

    async def fetch_history(self, session_id: str) -> list[dict]:
        """拉取会话历史轮次（AI 服务端 Redis 记忆），失败/无历史返回空列表。

        Returns:
            [{"role": "user"|"assistant", "content": str}, ...]
        """
        url = f"{self._base_url}/api/ai/memory/history"
        try:
            resp = await self._client.get(url, params={"session_id": session_id})
            resp.raise_for_status()
            data = resp.json()
            inner = (data.get("data") or {}) if data.get("code") == 0 else {}
            turns = inner.get("turns") or []
            history: list[dict] = []
            for t in turns:
                role = t.get("role")
                content = t.get("content")
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    history.append({"role": role, "content": content})
            return history
        except Exception as exc:
            logger.warning("拉取会话历史失败 sid=%s: %s", session_id, exc)
            return []

    async def add_turn(self, session_id: str, role: str, content: str) -> None:
        """显式追加一条会话记忆轮次（agentic 流程最终问答对落库用）。

        失败静默降级（记忆丢失不影响主流程）。
        """
        url = f"{self._base_url}/api/ai/memory/turn"
        try:
            resp = await self._client.post(
                url, json={"session_id": session_id, "role": role, "content": content}
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("追加会话记忆失败 sid=%s role=%s: %s", session_id, role, exc)

    # ── 便捷方法 ────────────────────────────────────────────

    @property
    def model_name(self) -> str:
        """当前配置的模型名称（仅展示用，实际模型由 AI 服务侧决定）。"""
        return self._model

    @property
    def provider_name(self) -> str:
        """当前提供商名称。"""
        return self._config.provider.value
