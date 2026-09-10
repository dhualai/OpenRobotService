# -*- coding: utf-8 -*-
"""dar 分析判定专用 LLM 客户端：缺省 deepseek-v4.1-flash-expires-on-0910。

L1 话题切分 / L3 忠实性 judge / 检索 no 判定都是离线周分析。
模型沿革：pro 在中转上频繁超时（跑生产 503 段半程断）→ 0909 16:55 换
deepseek-v4.1-flash-expires-on-0910；已判的 prod 419 段 + test 231 段都是它判的，
缺省保持不变，避免新旧段混模型。
该名不在网关受支持列表（只有 v4-pro / v4-flash / v4-flash-vision-exp，探活实锤），
属未文档化别名；名字自带 expires-on-0910 但未获官方确认是否真失效——别把它当事实。
若某天失效（400），dar_l3 起跑探活会明确报出当前模型名，届时
DAR_MODEL=deepseek-v4-flash 切到受支持正式名，判定行带 model 字段，
全量重判用工作台「重判 L3」按钮。
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
        model = os.getenv("DAR_MODEL", "deepseek-v4.1-flash-expires-on-0910")
        _dar_llm = LLMClient(provider=provider, model=model)
    return _dar_llm
