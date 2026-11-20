"""外部任务源集成层（INTEGRATION_DESIGN.md）。

Phase 3：按 ``TASK_SOURCES_ENABLED`` 装载任务源插件——import 插件包即触发
其 ``__init__.py`` 中的 ``registry.register(...)`` 自注册。
"""
import logging

from app.core.config import settings
from app.integrations.base import (
    ExternalTask,
    SyncResult,
    TaskSourceAdapter,
)
from app.integrations.engine import SyncEngine, merge_status
from app.integrations.registry import SourceRegistry, registry

logger = logging.getLogger(__name__)


def _load_sources() -> None:
    """按 TASK_SOURCES_ENABLED 装载任务源插件（import 即自注册）。"""
    for name in settings.TASK_SOURCES_ENABLED or []:
        try:
            __import__(f"app.integrations.sources.{name}", fromlist=["__init__"])
            logger.info("已装载任务源插件：%s", name)
        except ImportError as e:
            logger.warning("任务源插件 %s 装载失败（ImportError）：%s", name, e)
        except Exception:
            logger.exception("任务源插件 %s 注册异常", name)


_load_sources()

__all__ = [
    "ExternalTask",
    "SyncResult",
    "TaskSourceAdapter",
    "SourceRegistry",
    "registry",
    "SyncEngine",
    "merge_status",
]
