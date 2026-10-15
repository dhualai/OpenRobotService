"""CapabilityRegistry — 能力注册表

- 类级别注册：子类继承 BaseCapability 即自动注册（__init_subclass__）
- key 用 module.QualifiedName 保证唯一
- list_available() 只返回当前环境可用能力（过滤 is_available()==False）
- match_by_tags() 供 Router / Orchestrator 按标签/描述匹配能力（衔接 G1）
- get() 每次新建实例（不复用）：能力实例上的用量计数因此**不会跨调用累计**，
  所以 `max_usage_per_session` 从未生效过（详见 AiTaskPlatform/issues.md #10）
"""

from __future__ import annotations

from typing import Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability


class CapabilityRegistry:
    """能力注册表。类方法无需实例化，全进程共享一份。"""

    _capabilities: dict[str, Type["BaseCapability"]] = {}

    # ── 注册 ──
    @classmethod
    def register(cls, cap_cls: Type["BaseCapability"]) -> None:
        """注册能力类。key = module.QualifiedName（保证唯一）。"""
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
        """取能力实例（每次新建）。

        注：这里曾有一个 `_instantiate` 自定义实例化钩子（注释称 code_skill 用它复用
        单例），但全仓无任何能力类实现该属性，分支恒不生效；code_search 实际是在
        run() 内部自取 get_code_skill() 单例。已删除该死分支，避免误以为实例被复用。
        """
        cap_cls = cls._capabilities.get(name)
        if cap_cls is None:
            return None
        return cap_cls()

    @classmethod
    def limit_of(cls, name: str) -> Optional[int]:
        """读能力类声明的「每次请求调度上限」（不实例化能力类）。None = 不限。

        供 `capabilities/core/usage.py` 的配额机制使用。刻意读类属性：能力实例每次
        新建，任何挂在实例上的计数都活不过一次调用（issues.md #10）。
        """
        cap_cls = cls._capabilities.get(name)
        if cap_cls is None:
            return None
        return getattr(cap_cls, "max_usage_per_session", None)

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
