"""ArchiveReport service — 归档报告业务逻辑"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ArchiveReport, Task, TaskStatus
from app.core.ticket_roles import get_ticket_roles

logger = logging.getLogger(__name__)


def validate_knowledge_report(content):
    headings = ("问题概述", "问题场景", "复现条件", "原因分析", "解决方案", "验证结果", "适用范围", "遗留问题")
    pattern = r"(?m)^#{1,6}\s+(?:\d+[.、]\s*)?(" + "|".join(headings) + r")\s*$"
    matches = list(re.finditer(pattern, content or ""))
    sections = {match.group(1): content[match.end():matches[position + 1].start() if position + 1 < len(matches) else len(content)].strip()
                for position, match in enumerate(matches)}
    missing = [heading for heading in headings if not sections.get(heading)]
    if missing:
        from fastapi import HTTPException
        raise HTTPException(422, "归档报告缺少章节或章节正文：" + "、".join(missing))


async def require_assigned_reviewer(db, report, task, operator_id):
    from fastapi import HTTPException
    from app.modules.admin.services.knowledge_service import actor_identities

    if not task:
        raise HTTPException(404, "工单未找到")
    identities = await actor_identities(db, {"id": operator_id})
    if report.reviewer not in identities:
        raise HTTPException(403, "报告已改派或未分配给你，请刷新审核任务")
    if identities.intersection({task.created_by, task.assigned_to, report.created_by}):
        raise HTTPException(403, "不能审核本人参与的问题")


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
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id)
            .with_for_update().execution_options(populate_existing=True))
        report = result.scalar_one_or_none()
        if not report:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status in ('approved', 'submitted'):
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
            expand_exclude_ids,
        )

        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id)
            .with_for_update().execution_options(populate_existing=True))
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

        from app.modules.admin.services.knowledge_service import CAMPAIGN_START, CAMPAIGN_END, utcnow
        if CAMPAIGN_START <= task.created_at < CAMPAIGN_END and utcnow() < CAMPAIGN_END:
            validate_knowledge_report(content if content is not None else report.content)

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

        excluded = await expand_exclude_ids(db, [operator_id, task.created_by, task.assigned_to, report.created_by])
        if chosen in excluded:
            raise HTTPException(409, "没有符合回避条件的审核人，请管理员补充名单或改派")
        if content is not None:
            report.content = content
        report.version = (report.version or 1) + 1
        report.submit_status = 'submitted'
        report.reviewer = chosen
        report.reviewed_at = None
        report.review_comment = None
        report.value_point = None
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
    async def approve(db: AsyncSession, report_id: int, operator_id: str, review_comment: str,
                      value_point: str, revision: int) -> tuple[ArchiveReport, Task]:
        """审核通过：submit_status→approved，reviewed_at 写入，工单→ARCHIVED，archived_by/archived_at 写入。

        同时自动生成三条闭环奖励（reporter / assignee / reviewer），走审核决策引擎分流。
        """
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id)
            .with_for_update().execution_options(populate_existing=True))
        report = result.scalar_one_or_none()
        if not report:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status != 'submitted':
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"当前报告状态({report.submit_status})不可审核通过")

        from fastapi import HTTPException
        from app.models.knowledge import ArchiveReviewDecision
        from app.modules.admin.services.knowledge_service import publish_report, utcnow
        if report.revision != revision:
            raise HTTPException(409, "报告已更新，请刷新后审核")
        if not review_comment.strip() or not value_point.strip():
            raise HTTPException(422, "通过审核必须填写意见及价值点")
        task_result = await db.execute(select(Task).where(Task.id == report.task_id)
            .with_for_update().execution_options(populate_existing=True))
        task = task_result.scalar_one_or_none()
        await require_assigned_reviewer(db, report, task, operator_id)
        report.review_comment = review_comment.strip()
        report.value_point = value_point.strip()
        report.submit_status = 'approved'
        report.revision += 1
        report.reviewed_at = func.now()
        report.updated_by = operator_id

        if task:
            task.status = TaskStatus.ARCHIVED
            task.archived_by = operator_id
            task.archived_at = func.now()

            db.add(ArchiveReviewDecision(task_id=task.id, report_id=report.id, version=report.version,
                reviewer_id=operator_id, outcome="approved", review_comment=report.review_comment,
                value_point=report.value_point, content=report.content, created_at=utcnow()))
            await publish_report(db, task, report)

        # ── 自动生成闭环奖励（flush 前注入，保证 task/report/reward 同事务）──
        try:
            from app.modules.wxpay.schemas import GrantRewardIn
            from app.modules.wxpay.service import RewardService

            cfg = await RewardService.resolve_config(db, "task_archive")
            policy = await RewardService.resolve_config(db, "audit_policy")
            if cfg is not None and task is not None:
                task_id_str = str(task.id)
                scene_name = "工单闭环奖励"
                items: list[GrantRewardIn] = []

                # 提单人
                if task.created_by and int(cfg.get("reporter_fen", 0) or 0) > 0:
                    items.append(GrantRewardIn(
                        user_id=task.created_by,
                        amount=int(cfg["reporter_fen"]),
                        scene_name=scene_name,
                        reward_desc="工单提单人奖励",
                        source_type="task",
                        source_id=task_id_str,
                    ))
                # 接单人
                if task.assigned_to and int(cfg.get("assignee_fen", 0) or 0) > 0:
                    items.append(GrantRewardIn(
                        user_id=task.assigned_to,
                        amount=int(cfg["assignee_fen"]),
                        scene_name=scene_name,
                        reward_desc="工单接单人奖励",
                        source_type="task",
                        source_id=task_id_str,
                    ))
                # 审核人（即当前 operator_id，刚通过了解决报告审核）
                if operator_id and int(cfg.get("reviewer_fen", 0) or 0) > 0:
                    items.append(GrantRewardIn(
                        user_id=operator_id,
                        amount=int(cfg["reviewer_fen"]),
                        scene_name=scene_name,
                        reward_desc="归档审核人奖励",
                        source_type="task",
                        source_id=task_id_str,
                    ))

                if items:
                    await RewardService.grant_batch(
                        db, items, role="task", audit_policy=policy, auto_commit=False
                    )
        except Exception as e:
            from app.services.logging import get_logger
            get_logger(__name__).error(
                f"[reward] 闭环奖励自动发放失败: task_id={task.id if task else '?'} err={e}"
            )
            # 奖励发放失败不阻断归档主流程

        await db.flush()
        return report, task

    @staticmethod
    async def reject(db: AsyncSession, report_id: int, review_comment: str, operator_id: str,
                     revision: int) -> tuple[ArchiveReport, Task]:
        """审核驳回：submit_status→rejected，review_comment/reviewed_at 写入，工单仍保持 archiving，清空 task.archived_by"""
        result = await db.execute(select(ArchiveReport).where(ArchiveReport.id == report_id)
            .with_for_update().execution_options(populate_existing=True))
        report = result.scalar_one_or_none()
        if not report:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="归档报告未找到")
        if report.submit_status != 'submitted':
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"当前报告状态({report.submit_status})不可驳回")

        from fastapi import HTTPException
        from app.models.knowledge import ArchiveReviewDecision
        from app.modules.admin.services.knowledge_service import utcnow
        if report.revision != revision:
            raise HTTPException(409, "报告已更新，请刷新后审核")
        if not review_comment.strip():
            raise HTTPException(422, "驳回必须填写意见")
        task_result = await db.execute(select(Task).where(Task.id == report.task_id)
            .with_for_update().execution_options(populate_existing=True))
        task = task_result.scalar_one_or_none()
        await require_assigned_reviewer(db, report, task, operator_id)
        db.add(ArchiveReviewDecision(task_id=report.task_id, report_id=report.id, version=report.version,
            reviewer_id=operator_id, outcome="rejected", review_comment=review_comment.strip(),
            content=report.content, created_at=utcnow()))
        report.revision += 1
        report.value_point = None
        report.submit_status = 'rejected'
        report.review_comment = review_comment
        report.reviewed_at = func.now()
        report.updated_by = operator_id

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
