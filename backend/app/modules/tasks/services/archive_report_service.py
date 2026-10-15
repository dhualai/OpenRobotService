"""ArchiveReport service — 归档报告业务逻辑"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ArchiveReport, Task, TaskStatus
from app.core.ticket_roles import get_ticket_roles

logger = logging.getLogger(__name__)


class ArchiveReportService:
    @staticmethod
    async def start_archive(
        db: AsyncSession, task_id: int, operator_id: str
    ) -> tuple[Task, ArchiveReport, bool]:
        """开始归档：创建草稿并将工单状态置为 ARCHIVING。

        返回 (task, report, created)。created=False 表示已有未完成归档，幂等复用
        （避免「库已写入、响应失败」后再点变成 409）。
        """
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="工单未找到")

        # 幂等：已有未完成归档则直接复用
        existing = await db.execute(
            select(ArchiveReport)
            .where(ArchiveReport.task_id == task_id)
            .where(ArchiveReport.submit_status.in_(['draft', 'submitted', 'rejected']))
            .order_by(ArchiveReport.version.desc())
            .limit(1)
        )
        existing_report = existing.scalar_one_or_none()
        if existing_report:
            # 幂等复用未完成草稿；状态未落到 ARCHIVING 时一并回正
            if task.status != TaskStatus.ARCHIVING:
                task.status = TaskStatus.ARCHIVING
                await db.flush()
            return task, existing_report, False

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
        task.status = TaskStatus.ARCHIVING

        await db.flush()
        return task, report, True

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
    async def submit(
        db: AsyncSession,
        report_id: int,
        content: Optional[str],
        revision_check: Optional[int],
        operator_id: str,
        *,
        reviewer_id: Optional[str] = None,
    ) -> tuple[ArchiveReport, object]:
        """提交审核：系统派审选出审核人，只写 archive_reports.reviewer。

        不改 tasks.archived_by（仍由审核通过/驳回流程自行处理）。
        reviewer_id 参数保留兼容，正式路径忽略，由 archive_review 派审决定。
        返回 (report, assignment)。派审失败抛 HTTPException，不改状态。
        """
        from fastapi import HTTPException

        from app.modules.tasks.services.archive_review_dispatch import (
            assignment_log_detail,
            dispatch_archive_reviewer,
        )

        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id))
        report = result.scalar_one_or_none()
        if not report:
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status not in ('draft', 'rejected'):
            raise HTTPException(status_code=400, detail=f"当前报告状态({report.submit_status})不可提交")
        if revision_check is not None and report.revision != revision_check:
            raise HTTPException(status_code=409, detail="报告已被他人修改，请刷新后重试")

        task_result = await db.execute(select(Task).where(Task.id == report.task_id))
        task = task_result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="工单未找到")

        # 派审失败则不进入 submitted（正文若随请求传入也不落库，避免半提交）
        try:
            assignment = await dispatch_archive_reviewer(
                db,
                task=task,
                report=report,
                operator_id=operator_id,
                content_override=content,
            )
        except Exception as exc:
            # ArchiveReviewError 与其包装（业务性失败：无人可派、领域配置非法等）
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None) or str(exc) or "归档派审失败"
            if code:
                raise HTTPException(status_code=400, detail=message) from exc
            # 非业务异常（依赖缺失/导入失败/配置损坏…）→ 500。
            # 原来直接 `raise`：前端只看到光秃秃的 500，日志之外无线索，排查只能盲猜
            # （生产实际踩过：AI 侧模块未随产物部署 → ModuleNotFoundError → 500）。
            # 现在把异常类型与消息一并带出，并在服务端留完整堆栈。
            logger.exception(
                "归档派审失败（非业务异常）report_id=%s task_id=%s: %s: %s",
                report_id, getattr(task, "id", None), type(exc).__name__, exc,
            )
            raise HTTPException(
                status_code=500,
                detail=f"归档派审服务异常：{type(exc).__name__}: {message}",
            ) from exc

        chosen = assignment.reviewer_id
        # 兼容旧调用方：若派审未给出人且显式传了 reviewer，才回退（正常不应走到）
        if not chosen and reviewer_id:
            chosen = reviewer_id
        if not chosen:
            raise HTTPException(status_code=400, detail="没有可用审核人")

        if content is not None:
            report.content = content
        report.version = (report.version or 1) + 1
        report.submit_status = 'submitted'
        report.reviewer = chosen
        report.reviewed_at = None
        report.review_comment = None
        report.updated_by = operator_id
        report.revision = (report.revision or 1) + 1
        # 工单仍保持 archiving；不写 tasks.archived_by

        await db.flush()
        # 挂到 assignment 上供 API 写日志（避免再查一次）
        try:
            assignment._log_detail = assignment_log_detail(assignment)  # type: ignore[attr-defined]
        except Exception:
            pass
        return report, assignment

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
