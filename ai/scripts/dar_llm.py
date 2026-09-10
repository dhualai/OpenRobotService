# -*- coding: utf-8 -*-
"""dar 分析判定专用 LLM 客户端：默认 deepseek-v4-pro。

L1 话题切分 / L3 忠实性 judge / 检索 no 判定都是离线周分析，不吃线上延迟，
质量优先（flash 在判定类任务上摇摆，见 solution_distiller 0902 实锤）。
独立客户端不动全局单例；换模型改环境变量 DAR_MODEL。
"""
import os

_dar_llm = None


async def get_dar_client():
    global _dar_llm
    if _dar_llm is None:
        from ai.core.llm import LLMClient, LLMProvider
        backend = (os.getenv("LLM_BACKEND") or "deepseek").strip().lower()
        provider = (LLMProvider.RELAY if backend == "relay"
                    else LLMProvider.OPENAI if backend == "openai"
                    else LLMProvider.DEEPSEEK)
        model = os.getenv("DAR_MODEL", "deepseek-v4-pro")
        _dar_llm = LLMClient(provider=provider, model=model)
    return _dar_llm
