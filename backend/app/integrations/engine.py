"""通用同步引擎（INTEGRATION_DESIGN.md §3.3 / §4.2 / §4.3）。

源无关：upsert / 状态合并（取较后状态）/ 账号映射解析 全部在此完成，
插件不参与落库逻辑。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.base import ExternalTask, SyncResult
from app.integrations.registry import registry
from app.models.task import Task, TaskStatus, TaskUserMapping

logger = logging.getLogger(__name__)

# 状态进度序号：合并时取 max（INTEGRATION_DESIGN.md §4.2）。
# PENDING（暂停/挂起）与 IN_PROGRESS 同级，不视作前进，避免互相回退。
STATUS_ORD = {
    TaskStatus.NEW: 0,
    TaskStatus.IN_PROGRESS: 1,
    TaskStatus.PENDING: 1,
    TaskStatus.RESOLVED: 2,
    TaskStatus.CANCELED: 3,
    TaskStatus.CLOSED: 3,
}


def merge_status(local: TaskStatus, incoming: TaskStatus) -> Optional[TaskStatus]:
    """返回应写入的状态；None 表示本平台已领先/同级，不操作。

    单向同步：本平台状态从不回写外部源；外部源允许滞后。
    """
    if STATUS_ORD[incoming] <= STATUS_ORD[local]:
        return None
    return incoming


class SyncEngine:
    """消费 adapter.fetch() 产出的 ExternalTask，幂等 upsert 进 tasks 表。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def sync_source(self, source_name: str) -> SyncResult:
        adapter = registry.get(source_name)
        result = SyncResult(source=source_name)

        if not adapter.is_enabled():
            logger.warning("任务源 %s 未启用（is_enabled=False），跳过同步", source_name)
            return result

        async for ext in adapter.fetch():
            result.fetched += 1
            try:
                local = await self._find(source_name, ext.external_id)
                if local is None:
                    await self._create(source_name, ext, result)
                else:
                    await self._merge_update(source_name, local, ext, result)
            except Exception as exc:  # 单条失败不中断整批
                logger.exception("同步 %s 任务 %s 失败", source_name, ext.external_id)
                result.mark_failed(f"{ext.external_id}: {exc}")

        await self.db.commit()
        await adapter.on_sync_done(result)
        return result

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    async def _find(self, source: str, external_id: str) -> Optional[Task]:
        stmt = select(Task).where(Task.source == source, Task.external_id == external_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _resolve_user(self, source: str, external_account: Optional[str]) -> Optional[str]:
        """按 (source, external_account) 查 task_user_mapping，返回本平台 user_id。"""
        if not external_account:
            return None
        stmt = select(TaskUserMapping.local_user_id).where(
            TaskUserMapping.source == source,
            TaskUserMapping.external_account == external_account,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _apply_common_fields(self, task: Task, source: str, ext: ExternalTask) -> None:
        """把 ExternalTask 的通用字段写到 Task（不含 status，状态由合并规则处理）。"""
        task.source = source
        task.external_id = ext.external_id
        task.external_url = ext.url
        task.title = ext.title
        task.description = ext.description
        task.task_type = ext.task_type
        task.priority = ext.priority
        task.deadline_at = ext.deadline_at
        task.assigned_to = await self._resolve_user(source, ext.assigned_account)
        if ext.created_account:
            # 创建者未配置映射时回退为外部账号原值，便于追溯
            task.created_by = (
                await self._resolve_user(source, ext.created_account) or ext.created_account
            )
        # 源特有字段（工时/层级等）合并进 metadata_info，便于追溯
        meta = dict(task.metadata_info or {})
        if ext.extra:
            meta["external"] = ext.extra
        if ext.url:
            meta["external_url"] = ext.url
        task.metadata_info = meta or None

    async def _create(self, source: str, ext: ExternalTask, result: SyncResult) -> None:
        task = Task(
            source=source,
            external_id=ext.external_id,
            title=ext.title,
            description=ext.description,
            status=ext.status,                       # 新建：直接采用映射后状态
            created_by="system",                     # 兜底；_apply_common_fields 有映射则覆盖
            created_at=ext.created_at or func.now(),
        )
        await self._apply_common_fields(task, source, ext)
        self.db.add(task)
        await self.db.flush()
        result.mark_created()

    async def _merge_update(self, source: str, local: Task, ext: ExternalTask, result: SyncResult) -> None:
        await self._apply_common_fields(local, source, ext)
        new_status = merge_status(local.status, ext.status)
        if new_status is not None:
            local.status = new_status
            if new_status == TaskStatus.RESOLVED and local.resolved_at is None:
                local.resolved_at = func.now()
            elif new_status == TaskStatus.CANCELED and local.canceled_at is None:
                local.canceled_at = func.now()
            elif new_status == TaskStatus.CLOSED and local.closed_at is None:
                local.closed_at = func.now()
        local.updated_at = func.now()
        await self.db.flush()
        result.mark_updated()
