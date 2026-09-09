# -*- coding: utf-8 -*-
"""dar 分析判定专用 LLM 客户端：默认 deepseek-v4-flash（生产同款）。

L1 话题切分 / L3 忠实性 judge / 检索 no 判定都是离线周分析。
模型沿革：pro 在中转上频繁超时（跑生产 503 段半程断）→ 0909 换 flash 4.1
预览名 deepseek-v4.1-flash-expires-on-0910 → 该名 0910 过期，且正式名
deepseek-v4.1-flash 网关不认（探活实锤：支持列表只有 v4-pro / v4-flash）
→ 默认改回生产同款 deepseek-v4-flash：不过期、与线上回答同源，判得更贴生产。
要复跑 0909 口径可 DAR_MODEL=deepseek-v4.1-flash-expires-on-0910（过期后 400）。
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
        model = os.getenv("DAR_MODEL", "deepseek-v4-flash")
        _dar_llm = LLMClient(provider=provider, model=model)
    return _dar_llm
