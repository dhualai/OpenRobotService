"""外部任务源集成 —— 核心契约（INTEGRATION_DESIGN.md §3）。

本模块是「外部任务源」可插拔机制的稳定核心，不依赖任何具体源（zentao 等）：
- `ExternalTask`：源无关的中立任务表示；插件把外部数据翻译成此结构。
- `TaskSourceAdapter`：源插件需实现的接口（只实现 fetch）。
- `SyncResult`：一次同步的统计结果。

增删源不改本文件。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Optional

from app.models.task import TaskStatus, TaskPriority, TaskType


@dataclass
class ExternalTask:
    """源无关的任务中立表示。

    插件负责把外部数据映射到此结构（枚举映射是源特有知识，留在插件内）。
    `assigned_account` / `created_account` 保留外部账号原值，由 SyncEngine
    查通用 `task_user_mapping` 表解析为本平台 user_id。
    """

    external_id: str
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    task_type: TaskType
    assigned_account: Optional[str] = None
    created_account: Optional[str] = None
    created_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    url: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    """一次同步的统计结果。"""

    source: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)

    def mark_created(self) -> None:
        self.created += 1

    def mark_updated(self) -> None:
        self.updated += 1

    def mark_unchanged(self) -> None:
        self.unchanged += 1

    def mark_failed(self, err: str) -> None:
        self.failed += 1
        self.errors.append(err)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "errors": self.errors[:20],  # 截断，避免日志爆炸
        }


class TaskSourceAdapter(ABC):
    """外部任务源插件接口。

    子类只需实现 `fetch`（拉取 + 翻译成 ExternalTask）。
    upsert / 状态合并 / 账号映射 / 入库由 SyncEngine 统一完成。
    """

    name: str = ""          # 作为 tasks.source 字段值，如 "zentao"
    display_name: str = ""  # 展示名，如 "禅道"

    @abstractmethod
    def is_enabled(self) -> bool:
        """插件是否可用（配置缺失等情况下返回 False）。"""

    @abstractmethod
    async def fetch(self) -> AsyncIterator[ExternalTask]:
        """拉取外部任务并翻译为 ExternalTask。引擎消费此迭代器。"""

    async def on_sync_done(self, result: SyncResult) -> None:
        """同步完成回调钩子（记日志/发通知等），默认空实现。"""
        return None
