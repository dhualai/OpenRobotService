"""AI 数据分析平台 · 统一 LLM 客户端

封装 OpenAI SDK，统一对接 DeepSeek / Qwen / GLM / SiliconFlow 等在线开源大模型。
所有提供商均提供 OpenAI 兼容 API，因此只需切换 base_url 和 api_key。
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

from .config import AnalysisConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """统一大模型客户端。

    所有支持的提供商（DeepSeek、通义千问、智谱 GLM、硅基流动等）均提供
    OpenAI 兼容 API，通过 ``AsyncOpenAI`` 统一调用。

    用法::

        config = AnalysisConfig.from_env()
        client = LLMClient(config)
        result = await client.chat(system_prompt, user_prompt)
    """

    def __init__(self, config: AnalysisConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.provider_config.api_key,
            base_url=config.provider_config.base_url,
            timeout=config.settings.timeout,
        )
        self._model = config.provider_config.model
        self._temperature = config.settings.temperature
        self._max_tokens = config.settings.max_tokens

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
        """
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )

        content = response.choices[0].message.content or ""
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        logger.info(
            "LLM 调用完成 model=%s tokens=%s",
            self._model,
            usage.get("total_tokens") if usage else "N/A",
        )
        return content, usage

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
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ── 便捷方法 ────────────────────────────────────────────

    @property
    def model_name(self) -> str:
        """当前使用的模型名称。"""
        return self._model

    @property
    def provider_name(self) -> str:
        """当前提供商名称。"""
        return self._config.provider.value
