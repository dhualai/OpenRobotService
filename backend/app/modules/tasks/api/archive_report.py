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
    ArchiveReportResponse,
)
from app.modules.tasks.schemas.ticket import TicketResponse
from app.modules.tasks.api.ws import ws_broadcast_task_updated
from app.modules.tasks.api.task import _add_system_comment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/archive-report", tags=["archive-report"])


async def _enrich_report_response(db: AsyncSession, report: ArchiveReport) -> ArchiveReportResponse:
    """查用户表补全 created_by / updated_by / reviewer 的显示名。"""
    response = ArchiveReportResponse.model_validate(report)

    ids_to_lookup = {report.created_by, report.updated_by, report.reviewer} - {None, ""}
    if ids_to_lookup:
        result = await db.execute(
            select(UserDB.id, UserDB.username, UserDB.name).where(UserDB.id.in_(ids_to_lookup))
        )
        user_map: dict[str, str] = {}
        for row in result.all():
            display_name = row.name or row.username
            if display_name:
                user_map[row.id] = display_name

        response.created_by_name = user_map.get(report.created_by)
        response.updated_by_name = user_map.get(report.updated_by)
        response.reviewer_name = user_map.get(report.reviewer)

    return response


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

    # 3) 业务调用（内部 flush）
    task_new, report = await ArchiveReportService.start_archive(db, task_id, operator_id)

    # 4) 操作人显示名
    user_name = await _user_display_name(db, operator_id) or username

    # 5) 写操作日志
    await OperationLogService.log(
        db=db,
        task_id=task_id,
        op_type=OperationType.ARCHIVE_START,
        operator=username,
        operator_name=user_name,
        to_status=TaskStatus.ARCHIVING.value,
        detail={"to_report_id": report.id, "version": report.version},
        description=f"{user_name} 开始归档工单，状态变更为「归档中」",
    )

    # 6) _add_system_comment 内部会 commit 当前 session 的全部 pending 变更
    await _add_system_comment(
        db, task_id,
        f"{user_name} 开始归档工单，状态变更为「归档中」",
        username, token,
    )

    # 7) WS 广播
    await ws_broadcast_task_updated(task_id, task_new)

    # 8) 刷新返回
    await db.refresh(task_new)
    return TicketResponse.model_validate(task_new)


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
    return await _enrich_report_response(db, report)


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

    # 操作日志（复用 UPDATE）
    user_name = await _user_display_name(db, operator_id) or username
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.UPDATE,
        operator=username,
        operator_name=user_name,
        description=f"{user_name} 保存了归档报告草稿",
        detail={"report_id": report_id, "revision": updated_report.revision},
    )

    # commit（service 只 flush）
    await db.commit()
    await db.refresh(updated_report)

    return await _enrich_report_response(db, updated_report)


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

    # 业务调用
    updated_report = await ArchiveReportService.submit(
        db, report_id, body.reviewer, body.content, body.revision, operator_id
    )

    user_name = await _user_display_name(db, operator_id) or username
    reviewer_name = await _user_display_name(db, body.reviewer) or body.reviewer

    # 操作日志
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.ARCHIVE_REPORT_SUBMIT,
        operator=username,
        operator_name=user_name,
        description=f"{user_name} 提交了归档报告，指定 {reviewer_name} 为审核人",
        detail={"report_id": report_id, "reviewer": body.reviewer},
    )

    # 系统评论（内部 commit 全部 pending 变更）
    await _add_system_comment(
        db, report.task_id,
        f"{user_name} 提交了归档报告，指定 {reviewer_name} 为审核人",
        username, token,
    )

    await db.refresh(updated_report)
    return await _enrich_report_response(db, updated_report)


# ==================== 审核通过 ====================


@router.post("/archive-reports/{report_id}/approve")
async def approve_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user_from_token),
):
    """POST /tasks/archive-reports/{report_id}/approve — 审核通过（工单状态→ARCHIVED，终态）。"""
    operator_id, username, token = await _operator_info(current_user)

    report = await ArchiveReportService.get_by_id(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="归档报告未找到")

    # 权限：reviewer 本人或 admin
    if not (user_matches(current_user, report.reviewer) or is_admin_user(current_user)):
        raise HTTPException(status_code=403, detail="无权限审核该归档报告")

    # 业务调用
    approved_report, task_new = await ArchiveReportService.approve(db, report_id, operator_id)

    user_name = await _user_display_name(db, operator_id) or username

    # 操作日志
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.ARCHIVE_APPROVE,
        operator=username,
        operator_name=user_name,
        to_status=TaskStatus.ARCHIVED.value,
        description=f"{user_name} 审核通过归档报告，工单状态变更为「已归档」",
        detail={"report_id": report_id},
    )

    # 系统评论（内部 commit）
    await _add_system_comment(
        db, report.task_id,
        f"{user_name} 审核通过归档报告，工单状态变更为「已归档」",
        username, token,
    )

    # WS 广播
    await ws_broadcast_task_updated(report.task_id, task_new)

    await db.refresh(task_new)
    return TicketResponse.model_validate(task_new)


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
    if not (user_matches(current_user, report.reviewer) or is_admin_user(current_user)):
        raise HTTPException(status_code=403, detail="无权限审核该归档报告")

    # 业务调用
    rejected_report, task_new = await ArchiveReportService.reject(
        db, report_id, body.review_comment, operator_id
    )

    user_name = await _user_display_name(db, operator_id) or username

    # 操作日志
    await OperationLogService.log(
        db=db,
        task_id=report.task_id,
        op_type=OperationType.ARCHIVE_REJECT,
        operator=username,
        operator_name=user_name,
        description=f"{user_name} 驳回了归档报告",
        detail={"report_id": report_id, "reason": body.review_comment},
    )

    # 系统评论（内部 commit）
    await _add_system_comment(
        db, report.task_id,
        f"{user_name} 驳回了归档报告",
        username, token,
    )

    await db.refresh(rejected_report)
    return await _enrich_report_response(db, rejected_report)
