"""任务源注册表（INTEGRATION_DESIGN.md §3.3）。

插件包在 import 时调用 `registry.register(AdapterInstance())` 完成自注册；
引擎与路由通过 `registry.get(name)` 按名发现源。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.integrations.base import TaskSourceAdapter

logger = logging.getLogger(__name__)


class SourceRegistry:
    """任务源适配器注册表。"""

    def __init__(self) -> None:
        self._adapters: Dict[str, TaskSourceAdapter] = {}

    def register(self, adapter: TaskSourceAdapter) -> None:
        if not adapter.name:
            raise ValueError("TaskSourceAdapter.name 不能为空")
        if adapter.name in self._adapters:
            logger.warning("任务源 %s 已注册，覆盖旧实例", adapter.name)
        self._adapters[adapter.name] = adapter
        logger.info("已注册任务源：%s (%s)", adapter.name, adapter.display_name)

    def unregister(self, name: str) -> Optional[TaskSourceAdapter]:
        return self._adapters.pop(name, None)

    def get(self, name: str) -> TaskSourceAdapter:
        if name not in self._adapters:
            raise KeyError(f"未注册的任务源：{name}")
        return self._adapters[name]

    def has(self, name: str) -> bool:
        return name in self._adapters

    def all(self) -> List[TaskSourceAdapter]:
        return list(self._adapters.values())

    def names(self) -> List[str]:
        return list(self._adapters.keys())


# 全局单例
registry = SourceRegistry()
