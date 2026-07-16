"""禅道任务源插件（INTEGRATION_DESIGN.md Phase 2）。

import 本包即触发自注册：``registry.register(ZentaoAdapter())``。
实际的「按 TASK_SOURCES_ENABLED 装载」在 Phase 3 接入（见
INTEGRATION_DESIGN.md §7 自注册机制）。
"""
from app.integrations.registry import registry
from app.integrations.sources.zentao.adapter import ZentaoAdapter

registry.register(ZentaoAdapter())

__all__ = ["ZentaoAdapter"]
