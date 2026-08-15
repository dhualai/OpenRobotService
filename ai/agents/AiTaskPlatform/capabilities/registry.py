"""CapabilityRegistry — 能力注册表

- 类级别注册：子类继承 BaseCapability 即自动注册（__init_subclass__）
- key 用 module.QualifiedName 保证唯一（借鉴 CrewAI）
- list_available() 只返回当前环境可用能力（过滤 is_available()==False）
- match_by_tags() 供 Router / Orchestrator 按标签/描述匹配能力（衔接 G1）
- get_singleton() 复用能力单例（如 code_skill 的单例懒加载索引）
"""

from __future__ import annotations

from typing import Any, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from ai.agents.AiTaskPlatform.capabilities.base import BaseCapability


class CapabilityRegistry:
    """能力注册表。类方法无需实例化，全进程共享一份。"""

    _capabilities: dict[str, Type["BaseCapability"]] = {}

    # ── 注册 ──
    @classmethod
    def register(cls, cap_cls: Type["BaseCapability"]) -> None:
        """注册能力类。key = module.QualifiedName（保证唯一，借鉴 CrewAI）。"""
        key = f"{cap_cls.__module__}.{cap_cls.__qualname__}"
        # 若同一 class 被重复 import/注册，幂等覆盖
        if key not in cls._capabilities:
            cls._capabilities[key] = cap_cls
            cls._capabilities[cap_cls.name] = cap_cls  # 也注册一个 name 别名

    # ── 查询 ──
    @classmethod
    def list(cls) -> list[str]:
        """全部能力名清单（去重，name 别名），适合调试/展示。"""
        # 只取非 module.QualifiedName 形式的 name 别名
        return [n for n in cls._capabilities if "." not in n]

    @classmethod
    def list_available(cls) -> list[str]:
        """仅当前环境可用能力名（过滤 is_available()==False）。"""
        return [n for n in cls.list() if cls.get(n).is_available()]

    @classmethod
    def get(cls, name: str) -> Optional["BaseCapability"]:
        """取能力实例。优先复用单例（能力类可提供 _instantiate）。"""
        cap_cls = cls._capabilities.get(name)
        if cap_cls is None:
            return None
        # 支持能力类自定义实例化（如 code_skill 用 get_code_skill() 单例）
        inst_factory = getattr(cap_cls, "_instantiate", None)
        if callable(inst_factory):
            inst = inst_factory()
        else:
            inst = cap_cls()
        return inst

    @classmethod
    def match_by_tags(cls, query: str) -> list[str]:
        """按标签/描述关键词匹配能力（供 Router 用）。

        简单实现：query 命中某能力的 tags 或 description 关键词 → 返回该能力名。
        只返回 is_available() 的能力。后续可升级为打分排序。
        """
        q = (query or "").lower()
        matched = []
        for name in cls.list():
            cap = cls.get(name)
            if not cap or not cap.is_available():
                continue
            # 标签匹配（大小写不敏感）
            if any(tag in q for tag in cap.tags):
                matched.append(name)
                continue
            # 描述关键词匹配（用能力名 + description 前 200 字符里的 token）
            desc = f"{cap.name} {cap.description}".lower()
            # 简单：query 里的词若出现在描述里则匹配
            for token in _tokenize(q):
                if token and token in desc:
                    matched.append(name)
                    break
        return matched


def _tokenize(text: str) -> list[str]:
    """极简分词：按非字母数字切分，去掉空串和单字符。够 Router 用即可。"""
    import re
    return [t for t in re.split(r"[^\u4e00-\u9fa5a-zA-Z0-9]+", text) if t and len(t) > 1]


# ── 便捷函数 ──
def get_capability(name: str) -> Optional["BaseCapability"]:
    """按名称获取能力实例（便捷入口）。"""
    return CapabilityRegistry.get(name)
