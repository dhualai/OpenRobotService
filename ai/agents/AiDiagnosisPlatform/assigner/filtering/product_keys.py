"""产品画像 key 别名：config 产品名 ↔ users.responsibility_modules 中的 key。"""

from __future__ import annotations

from typing import Dict, List

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile


def profile_keys_for_product(config: AssignerConfig, product: str) -> List[str]:
    """返回该产品在工程师画像中可能出现的 responsibility_modules key 列表。"""
    if not product:
        return []
    routing = config.product_routing or {}
    profile_keys: Dict[str, list] = routing.get("profile_keys") or {}
    keys = profile_keys.get(product)
    if keys:
        return list(keys)
    return [product]


def engineer_has_product(eng: EngineerProfile, product: str, config: AssignerConfig) -> bool:
    rm = eng.responsibility_modules or {}
    return any(k in rm for k in profile_keys_for_product(config, product))


def engineer_modules_for_product(
    eng: EngineerProfile, product: str, config: AssignerConfig,
) -> List[str]:
    """返回该产品下工程师负责的【功能名】扁平列表（三层 {产品:{界面:[功能]}} 展开）。

    兼容旧两层/旧 list 数据：若某产品的值直接是 list 则原样透传。
    供 semantic_recall 等以"功能名"为粒度消费（与 module_classify 对齐）。
    """
    rm = eng.responsibility_modules or {}
    mods: List[str] = []
    for k in profile_keys_for_product(config, product):
        by_iface = rm.get(k)
        if by_iface is None:
            continue
        if isinstance(by_iface, dict):
            for iface, funcs in by_iface.items():
                fns = funcs if isinstance(funcs, list) else [funcs]
                for f in fns:
                    if f:
                        mods.append(f)
        else:  # 兼容旧两层/旧 list
            mods.extend(by_iface if isinstance(by_iface, list) else [by_iface])
    return mods


def classify_map_for_product(config: AssignerConfig, product: str) -> Dict[str, str]:
    """合并 canonical 产品与 alias key 的 module_classify 映射。"""
    merged: Dict[str, str] = {}
    module_classify = config.module_classify or {}
    for k in profile_keys_for_product(config, product):
        merged.update(module_classify.get(k) or {})
    merged.update(module_classify.get(product) or {})
    return merged
