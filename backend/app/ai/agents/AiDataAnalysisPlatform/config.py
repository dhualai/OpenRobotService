"""AI 数据分析平台 · 配置模块

通过环境变量读取 LLM 提供商配置，支持 DeepSeek / Qwen / GLM / SiliconFlow
及任意 OpenAI 兼容接口的在线开源大模型。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LLMProvider(str, Enum):
    """支持的 LLM 提供商。"""

    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    GLM = "glm"
    SILICONFLOW = "siliconflow"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass
class ProviderConfig:
    """单个提供商的连接配置。"""

    api_key: str
    base_url: str
    model: str


# ── 各提供商默认配置 ──────────────────────────────────────────

_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    LLMProvider.DEEPSEEK.value: {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL",
        "default_base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    LLMProvider.QWEN.value: {
        "api_key_env": "QWEN_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "model_env": "QWEN_MODEL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    LLMProvider.GLM.value: {
        "api_key_env": "GLM_API_KEY",
        "base_url_env": "GLM_BASE_URL",
        "model_env": "GLM_MODEL",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
    },
    LLMProvider.SILICONFLOW.value: {
        "api_key_env": "SILICONFLOW_API_KEY",
        "base_url_env": "SILICONFLOW_BASE_URL",
        "model_env": "SILICONFLOW_MODEL",
        "default_base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
    },
    LLMProvider.OPENAI_COMPATIBLE.value: {
        "api_key_env": "LLM_API_KEY",
        "base_url_env": "LLM_BASE_URL",
        "model_env": "LLM_MODEL",
        "default_base_url": "",
        "default_model": "",
    },
}


@dataclass
class LLMSettings:
    """LLM 调用参数。"""

    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: int = 120


@dataclass
class AnalysisConfig:
    """AI 数据分析平台全局配置。"""

    provider: LLMProvider
    provider_config: ProviderConfig
    settings: LLMSettings = field(default_factory=LLMSettings)

    @classmethod
    def from_env(cls) -> "AnalysisConfig":
        """从环境变量构建配置实例。"""
        provider_str = os.getenv("LLM_PROVIDER", LLMProvider.DEEPSEEK.value)
        try:
            provider = LLMProvider(provider_str)
        except ValueError:
            raise ValueError(
                f"不支持的 LLM_PROVIDER='{provider_str}'，"
                f"可选值: {[p.value for p in LLMProvider]}"
            )

        defaults = _PROVIDER_DEFAULTS[provider.value]

        api_key = os.getenv(defaults["api_key_env"], "")
        base_url = os.getenv(
            defaults["base_url_env"], defaults["default_base_url"]
        )
        model = os.getenv(defaults["model_env"], defaults["default_model"])

        if not api_key:
            raise ValueError(
                f"未配置 API Key：环境变量 {defaults['api_key_env']} 为空，"
                f"请参考 .env.example 填写提供商 '{provider.value}' 的密钥。"
            )
        if not model:
            raise ValueError(
                f"未配置模型名称：环境变量 {defaults['model_env']} 为空。"
            )

        settings = LLMSettings(
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            timeout=int(os.getenv("LLM_TIMEOUT", "120")),
        )

        return cls(
            provider=provider,
            provider_config=ProviderConfig(
                api_key=api_key, base_url=base_url, model=model
            ),
            settings=settings,
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（隐藏密钥）。"""
        return {
            "provider": self.provider.value,
            "model": self.provider_config.model,
            "base_url": self.provider_config.base_url,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "timeout": self.settings.timeout,
        }
