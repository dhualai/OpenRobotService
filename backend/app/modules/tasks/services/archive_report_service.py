"""ArchiveReport service — 归档报告业务逻辑"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ArchiveReport, Task, TaskStatus
from app.core.ticket_roles import get_ticket_roles


class ArchiveReportService:
    @staticmethod
    async def start_archive(db: AsyncSession, task_id: int, operator_id: str) -> tuple[Task, ArchiveReport]:
        """开始归档：创建 version=1 的草稿，工单状态置为 ARCHIVING。

        幂等检查：若该工单已存在 submit_status IN ('draft', 'submitted', 'rejected') 的 ArchiveReport，
        说明已有未完成的归档流程，返回 ConflictError 让调用方决定提示用户。
        """
        # 检查是否已有未完成的归档流程
        existing = await db.execute(
            select(ArchiveReport)
            .where(ArchiveReport.task_id == task_id)
            .where(ArchiveReport.submit_status.in_(['draft', 'submitted', 'rejected']))
            .limit(1)
        )
        if existing.scalar_one_or_none():
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="该工单已存在未完成的归档流程")

        # 取最新一版（可能是之前 approved 后又 reopen 工单的历史）
        latest = await db.execute(
            select(ArchiveReport)
            .where(ArchiveReport.task_id == task_id)
            .order_by(ArchiveReport.version.desc())
            .limit(1)
        )
        base_version = latest.scalar_one_or_none()
        next_version = (base_version.version if base_version else 0) + 1

        report = ArchiveReport(
            task_id=task_id,
            content="",
            version=next_version,
            created_by=operator_id,
            updated_by=operator_id,
            submit_status='draft',
            revision=1,
        )
        db.add(report)

        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="工单未找到")
        task.status = TaskStatus.ARCHIVING

        await db.flush()
        return task, report

    @staticmethod
    async def save_draft(db: AsyncSession, report_id: int, content: str, revision_check: Optional[int], operator_id: str) -> ArchiveReport:
        """保存草稿（revision 乐观锁）"""
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id))
        report = result.scalar_one_or_none()
        if not report:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status == 'approved':
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="已归档的报告不可修改")
        if revision_check is not None and report.revision != revision_check:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail=f"报告已被他人修改（期望 revision={revision_check}，实际={report.revision}），请刷新后重试")

        report.content = content
        report.updated_by = operator_id
        report.revision = (report.revision or 1) + 1
        await db.flush()
        return report

    @staticmethod
    async def submit(db: AsyncSession, report_id: int, reviewer_id: str, content: Optional[str], revision_check: Optional[int], operator_id: str) -> ArchiveReport:
        """提交审核：version+1，submit_status→submitted，reviewer 写入"""
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id))
        report = result.scalar_one_or_none()
        if not report:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status not in ('draft', 'rejected'):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"当前报告状态({report.submit_status})不可提交")
        if revision_check is not None and report.revision != revision_check:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="报告已被他人修改，请刷新后重试")

        report.version = (report.version or 1) + 1
        report.submit_status = 'submitted'
        report.reviewer = reviewer_id
        report.reviewed_at = None
        report.review_comment = None
        if content is not None:
            report.content = content
        report.updated_by = operator_id
        report.revision = (report.revision or 1) + 1
        await db.flush()
        return report

    @staticmethod
    async def approve(db: AsyncSession, report_id: int, operator_id: str) -> tuple[ArchiveReport, Task]:
        """审核通过：submit_status→approved，reviewed_at 写入，工单→ARCHIVED，archived_by/archived_at 写入"""
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id))
        report = result.scalar_one_or_none()
        if not report:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status != 'submitted':
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"当前报告状态({report.submit_status})不可审核通过")

        report.submit_status = 'approved'
        report.reviewed_at = func.now()
        report.updated_by = operator_id

        task_result = await db.execute(select(Task).where(Task.id == report.task_id))
        task = task_result.scalar_one_or_none()
        if task:
            task.status = TaskStatus.ARCHIVED
            task.archived_by = operator_id
            task.archived_at = func.now()

        await db.flush()
        return report, task

    @staticmethod
    async def reject(db: AsyncSession, report_id: int, review_comment: str, operator_id: str) -> tuple[ArchiveReport, Task]:
        """审核驳回：submit_status→rejected，review_comment/reviewed_at 写入，工单仍保持 archiving，清空 task.archived_by"""
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id))
        report = result.scalar_one_or_none()
        if not report:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status != 'submitted':
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"当前报告状态({report.submit_status})不可驳回")

        report.submit_status = 'rejected'
        report.review_comment = review_comment
        report.reviewed_at = func.now()
        report.updated_by = operator_id

        task_result = await db.execute(select(Task).where(Task.id == report.task_id))
        task = task_result.scalar_one_or_none()
        if task:
            task.archived_by = None

        await db.flush()
        return report, task

    @staticmethod
    async def get_latest_by_task(db: AsyncSession, task_id: int) -> Optional[ArchiveReport]:
        result = await db.execute(
            select(ArchiveReport)
            .where(ArchiveReport.task_id == task_id)
            .order_by(ArchiveReport.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(db: AsyncSession, report_id: int) -> Optional[ArchiveReport]:
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id))
        return result.scalar_one_or_none()
