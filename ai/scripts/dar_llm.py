# -*- coding: utf-8 -*-
"""dar 分析判定专用 LLM 客户端：缺省 deepseek-flash。

L1 话题切分 / L3 忠实性 judge / 检索 no 判定都是离线周分析。
模型沿革：pro 在中转上频繁超时（跑生产 503 段半程断）→ 0909 换未文档化别名
deepseek-v4.1-flash-expires-on-0910（prod 419 段 + test 231 段是它判的）→
**0910 别名 400 失效**，与生产 .env 一致切 deepseek-flash。
判定行带 model 字段，增量复用遇到异模型段会提示；全量重判用工作台
「重判 L3」按钮。
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
        model = os.getenv("DAR_MODEL", "deepseek-flash")
        _dar_llm = LLMClient(provider=provider, model=model)
    return _dar_llm
