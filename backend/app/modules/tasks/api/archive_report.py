"""归档报告 API 路由（archive workflow）。

承接工单关闭后的归档流程：
  开始归档 → 编辑/保存草稿 → 提交审核 → 审核通过(归档) / 审核驳回(退回草稿)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.core.auth_routes import get_current_active_user_from_token
from app.core.user_identity import user_matches, is_admin_user, actor_username
from app.core.ticket_roles import get_ticket_roles
from app.models.task import OperationType, TaskStatus, Task, ArchiveReport
from app.models.identity import UserDB
from app.modules.tasks.services.archive_report_service import ArchiveReportService
from app.modules.tasks.services.operation_log_service import OperationLogService
from app.modules.tasks.schemas.archive_report import (
    ArchiveReportSaveRequest,
    ArchiveReportSubmitRequest,
    ArchiveReportRejectRequest,
    ArchiveReportApproveRequest,
    ArchiveReportResponse,
)
from app.modules.tasks.schemas.ticket import TicketResponse
from app.modules.tasks.api.ws import ws_broadcast_task_updated
from app.modules.tasks.api.task import _add_system_comment
from app.modules.tasks.services.ticket_service import TicketService

logger = logging.getLogger(__name__)

from app.modules.tasks.services.knowledge_review import (
    pending_reviews, own_review_history, projected_report, review_detail,
)

# prefix=/archive-report：避免被 GET /tasks/{task_id} 抢路径；与前端 /archive-report/... 对齐
router = APIRouter(prefix="/archive-report", tags=["archive-report"])


async def _enrich_report_response(db: AsyncSession, report: ArchiveReport, actor=None) -> ArchiveReportResponse:
    """查用户表补全 created_by / updated_by / reviewer 的显示名。"""
    return await projected_report(db, report, actor or {})


@router.get("/reviews")
async def my_pending_reviews(db=Depends(get_db), actor=Depends(get_current_active_user_from_token)):
    return await pending_reviews(db, actor)


@router.get("/my-review-history")
async def my_review_history(db=Depends(get_db), actor=Depends(get_current_active_user_from_token)):
    return await own_review_history(db, actor)


@router.get("/reviews/{report_id}")
async def anonymous_review(report_id: int, db=Depends(get_db), actor=Depends(get_current_active_user_from_token)):
    return await review_detail(db, actor, report_id)


async def _operator_info(current_user) -> tuple[str, str, str]:
    """返回 (operator_id, username, token)。"""
    if isinstance(current_user, dict):
        uid = str(current_user.get("id") or current_user.get("user_id") or "")
        uname = str(current_user.get("username") or "")
        token = current_user.get("token") or ""
    else:
        uid = str(getattr(current_user, "id", "") or "")
        uname = str(getattr(current_user, "username", "") or "")
        token = getattr(current_user, "token", None) or ""
    return uid, uname, token


async def _user_display_name(db: AsyncSession, user_id: Optional[str]) -> str:
    if not user_id:
        return ""
    result = await db.execute(select(UserDB).where(UserDB.id == user_id).limit(1))
    user = result.scalar_one_or_none()
    if user:
        return user.name or user.username or user_id
    return user_id


# ==================== 开始归档 ====================


@router.post("/{task_id}/archive-start")
async def archive_start(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user_from_token),
):
    """POST /tasks/{task_id}/archive-start — 开始归档流程（创建 v1 草稿 + 工单状态→ARCHIVING）。"""
    # 1) 查工单是否存在
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="工单未找到")

    # 2) 权限：isAssignee 或 admin 或 has backend:tasks:operate
    roles = await get_ticket_roles(db, task, current_user)
    operator_id, username, token = await _operator_info(current_user)

    if not (roles.is_assignee or roles.is_admin or roles.can_operate):
        raise HTTPException(status_code=403, detail="无权限开始归档")

    try:
        # 3) 业务调用（内部 flush）；created=False 为幂等复用已有草稿
        task_new, report, created = await ArchiveReportService.start_archive(
            db, task_id, operator_id
        )

        if created:
            # 4) 操作人显示名
            user_name = await _user_display_name(db, operator_id) or username

            # 5) 写操作日志（不中途 commit，与业务变更一并提交）
            await OperationLogService.log(
                db=db,
                task_id=task_id,
                op_type=OperationType.ARCHIVE_START,
                operator=username,
                operator_name=user_name,
                to_status=TaskStatus.ARCHIVING.value,
                detail={"to_report_id": report.id, "version": report.version},
                description=f"{user_name} 开始归档工单，状态变更为「归档中」",
                auto_commit=False,
            )
            await db.commit()

            # 6) 系统评论单独 commit，失败不回滚归档业务
            await _add_system_comment(
                db, task_id,
                f"{user_name} 开始归档工单，状态变更为「归档中」",
                username, token,
            )

            # 7) WS 广播
            await ws_broadcast_task_updated(task_id, task_new)
        else:
            # 幂等路径也要 commit 可能的状态回正
            await db.commit()

        # 8) 统一走 TicketService reload（自动带 comments + current_archive_report enrich）
        ticket = await TicketService.get_ticket_by_id(db, task_id, load_comments=True, token=token)
        return TicketResponse.model_validate(ticket)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("archive-start failed task_id=%s", task_id)
        # 常见：缺 archive_reports 表 / status 枚举无 archiving / 缺 archived_* 列
        detail = str(getattr(e, "orig", None) or e)
        raise HTTPException(
            status_code=500,
            detail=f"开始归档失败（请检查库结构是否已执行 ensure_archive_schema.py）: {detail}",
        ) from e


# ==================== 查询 / 读取 ====================


@router.get("/{task_id}/archive-report")
async def get_latest_archive_report(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user_from_token),
):
    """GET /tasks/{task_id}/archive-report — 取该工单最新版归档报告（查看权限开放）。"""
    report = await ArchiveReportService.get_latest_by_task(db, task_id)
    if not report:
        raise HTTPException(status_code=404, detail="该工单暂无归档报告")
    task = await db.get(Task, task_id)
    if report.submit_status != "approved" and not user_matches(
        current_user, report.created_by, report.reviewer, task.created_by, task.assigned_to
    ):
        raise HTTPException(status_code=403, detail="无权限查看该报告")
    return await _enrich_report_response(db, report, current_user)


# ==================== 保存草稿 ====================


@router.put("/archive-reports/{report_id}")
async def save_draft(
    report_id: int,
    body: ArchiveReportSaveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user_from_token),
):
    """PUT /tasks/archive-reports/{report_id} — 保存草稿（PUT 不触发状态机）。"""
    operator_id, username, token = await _operator_info(current_user)

    # 先取报告判定权限
    report = await ArchiveReportService.get_by_id(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="归档报告未找到")

    # 权限：created_by 或 admin 或 can_operate
    result = await db.execute(select(Task).where(Task.id == report.task_id))
    task = result.scalar_one_or_none()
    roles = await get_ticket_roles(db, task, current_user) if task else None

    can_edit = (
        user_matches(current_user, report.created_by)
        or (roles and (roles.is_admin or roles.can_operate))
    )
    if not can_edit:
        raise HTTPException(status_code=403, detail="无权限编辑该归档报告")

    # 业务调用
    updated_report = await ArchiveReportService.save_draft(
        db, report_id, body.content, body.revision, operator_id
    )

    # 操作日志（复用 UPDATE；与草稿一并 commit）
    user_name = await _user_display_name(db, operator_id) or username
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.UPDATE,
        operator=username,
        operator_name=user_name,
        description=f"{user_name} 保存了归档报告草稿",
        detail={"report_id": report_id, "revision": updated_report.revision},
        auto_commit=False,
    )

    await db.commit()
    await db.refresh(updated_report)

    return await _enrich_report_response(db, updated_report, current_user)


# ==================== 提交审核 ====================


@router.post("/archive-reports/{report_id}/submit")
async def submit_report(
    report_id: int,
    body: ArchiveReportSubmitRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user_from_token),
):
    """POST /tasks/archive-reports/{report_id}/submit — 提交审核（draft/rejected → submitted）。"""
    operator_id, username, token = await _operator_info(current_user)

    report = await ArchiveReportService.get_by_id(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="归档报告未找到")

    # 权限：created_by 或 admin
    if not (user_matches(current_user, report.created_by) or is_admin_user(current_user)):
        raise HTTPException(status_code=403, detail="无权限提交该归档报告")

    # 业务调用：系统派审选审核人，只写 archive_reports.reviewer
    updated_report, assignment = await ArchiveReportService.submit(
        db, report_id, body.content, body.revision, operator_id
    )

    user_name = await _user_display_name(db, operator_id) or username
    log_detail = {"report_id": report_id}

    # 操作日志（含派审负荷/概率快照；与 submit 业务一并 commit）
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.ARCHIVE_REPORT_SUBMIT,
        operator=username,
        operator_name=user_name,
        description=(
            "已提交归档报告，等待匿名审核"
        ),
        detail=log_detail,
        auto_commit=False,
    )
    await db.commit()
    await db.refresh(updated_report)

    # 系统评论单独 commit，失败不回滚派审结果
    await _add_system_comment(
        db, report.task_id,
        "已提交归档报告，等待匿名审核",
        username, token,
    )

    return await _enrich_report_response(db, updated_report, current_user)


# ==================== 审核通过 ====================


@router.post("/archive-reports/{report_id}/approve")
async def approve_report(
    report_id: int,
    body: ArchiveReportApproveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user_from_token),
):
    """POST /tasks/archive-reports/{report_id}/approve — 审核通过（工单状态→ARCHIVED，终态）。"""
    operator_id, username, token = await _operator_info(current_user)

    report = await ArchiveReportService.get_by_id(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="归档报告未找到")

    # 权限：reviewer 本人或 admin
    if not user_matches(current_user, report.reviewer):
        raise HTTPException(status_code=403, detail="无权限审核该归档报告")

    # 业务调用
    task = await db.get(Task, report.task_id)
    if user_matches(current_user, task.created_by, task.assigned_to, report.created_by):
        raise HTTPException(403, "不能审核本人参与的问题")
    approved_report, task_new = await ArchiveReportService.approve(
        db, report_id, operator_id, body.review_comment, body.value_point, body.revision
    )

    user_name = await _user_display_name(db, operator_id) or username

    # 操作日志（与 approve 业务一并 commit）
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.ARCHIVE_APPROVE,
        operator="knowledge_review",
        operator_name="匿名审核人",
        to_status=TaskStatus.ARCHIVED.value,
        description="归档报告审核通过，工单状态变更为「已归档」",
        detail={"report_id": report_id},
        auto_commit=False,
    )
    await db.commit()

    # 系统评论单独 commit
    await _add_system_comment(
        db, report.task_id,
        "归档报告审核通过，工单状态变更为「已归档」",
        "system", None,
    )

    # WS 广播
    await ws_broadcast_task_updated(report.task_id, task_new)

    return {"report_id": report_id, "submit_status": "approved"}


# ==================== 审核驳回 ====================


@router.post("/archive-reports/{report_id}/reject")
async def reject_report(
    report_id: int,
    body: ArchiveReportRejectRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user_from_token),
):
    """POST /tasks/archive-reports/{report_id}/reject — 审核驳回（工单保持 archiving，
    清空 task.archived_by 残留字段）。"""
    operator_id, username, token = await _operator_info(current_user)

    report = await ArchiveReportService.get_by_id(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="归档报告未找到")

    # 权限：reviewer 本人或 admin
    if not user_matches(current_user, report.reviewer):
        raise HTTPException(status_code=403, detail="无权限审核该归档报告")

    # 业务调用
    task = await db.get(Task, report.task_id)
    if user_matches(current_user, task.created_by, task.assigned_to, report.created_by):
        raise HTTPException(403, "不能审核本人参与的问题")
    rejected_report, task_new = await ArchiveReportService.reject(
        db, report_id, body.review_comment, operator_id, body.revision
    )

    user_name = await _user_display_name(db, operator_id) or username

    # 操作日志（与 reject 业务一并 commit）
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.ARCHIVE_REJECT,
        operator="knowledge_review",
        operator_name="匿名审核人",
        description="归档报告已驳回，请当事人查看意见后补充",
        detail={"report_id": report_id},
        auto_commit=False,
    )
    await db.commit()
    await db.refresh(rejected_report)

    # 系统评论单独 commit
    await _add_system_comment(
        db, report.task_id,
        "归档报告已驳回，请当事人查看意见后补充",
        "system", None,
    )

    return {"report_id": report_id, "submit_status": "rejected"}
