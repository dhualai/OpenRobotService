# -*- coding: utf-8 -*-
"""dar 分析判定专用 LLM 客户端：默认 deepseek-v4-flash（网关受支持列表里的正式名）。

L1 话题切分 / L3 忠实性 judge / 检索 no 判定都是离线周分析。
模型沿革：pro 在中转上频繁超时（跑生产 503 段半程断）→ 0909 换
deepseek-v4.1-flash-expires-on-0910（当时实测可用）→ 该名不在网关受支持列表
（列表只有 v4-pro / v4-flash / v4-flash-vision-exp，探活实锤），属未文档化别名，
名字自带 expires-on-0910 但**未获官方确认**是否真按此失效 → 缺省改回受支持的
deepseek-v4-flash：与线上回答同源，判得更贴生产。
要复跑 0909 口径可 DAR_MODEL=deepseek-v4.1-flash-expires-on-0910（别名若失效会 400）。
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
