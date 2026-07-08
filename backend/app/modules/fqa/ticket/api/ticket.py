from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from datetime import datetime

from app.core.database import get_async_db as get_db
from app.modules.fqa.ticket.schemas.ticket import (
    TicketCreate, TicketUpdate, TicketResponse, TicketListResponse,
    TicketCommentCreate, TicketCommentUpdate, TicketCommentResponse,
    TicketQueryParams, TicketCuibanNotification, TicketFilterRequest
)
from app.modules.fqa.ticket.models.ticket import TicketStatus, TicketPriority, TicketType
from app.modules.fqa.ticket.services.ticket_service import TicketService
from app.modules.fqa.utils.minio_client import minio_client
from app.modules.fqa.utils.notification_utils import NotificationUtils
from app.core.config import settings

router = APIRouter(prefix="/tickets", tags=["tickets"])

comment_attachment_map = {}


@router.post("/", response_model=TicketResponse)
async def create_ticket(
    ticket_data: TicketCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    try:
        username = current_user.username if current_user else "system"
        token = getattr(current_user, 'token', None)
        ticket = await TicketService.create_ticket(db, ticket_data, username, comment_attachment_map, token)
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建工单失败: {str(e)}")


@router.get("/", response_model=TicketListResponse)
async def get_tickets(
    request: Request,
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(10, ge=1, le=100, description="每页数量"),
    id: Optional[int] = Query(None, description="工单ID"),
    id_op: Optional[str] = Query(None, description="工单ID过滤操作：equals|gt|gte|lt|lte|ne"),
    title: Optional[str] = Query(None, description="工单标题"),
    title_op: Optional[str] = Query(None, description="标题过滤操作：equals|contains|notEquals"),
    status: Optional[str] = Query(None, description="工单状态，支持多个状态用逗号分隔"),
    priority: Optional[str] = Query(None, description="工单优先级"),
    ticket_type: Optional[str] = Query(None, description="工单类型"),
    created_by: Optional[str] = Query(None, description="创建者ID"),
    created_by_op: Optional[str] = Query(None, description="创建者过滤操作"),
    created_by_name: Optional[str] = Query(None, description="创建者姓名"),
    assigned_to: Optional[str] = Query(None, description="处理者ID"),
    assigned_to_op: Optional[str] = Query(None, description="处理者过滤操作"),
    assigned_to_name: Optional[str] = Query(None, description="处理者姓名"),
    customer: Optional[str] = Query(None, description="客户信息"),
    customer_op: Optional[str] = Query(None, description="客户过滤操作"),
    customer_name: Optional[str] = Query(None, description="客户姓名"),
    related_resource_id: Optional[int] = Query(None, description="关联资源ID"),
    related_resource_id_op: Optional[str] = Query(None, description="关联资源ID过滤操作"),
    keyword: Optional[str] = Query(None, description="关键词搜索"),
    tag: Optional[str] = Query(None, description="标签过滤"),
    project_name: Optional[str] = Query(None, description="项目名称"),
    project_name_op: Optional[str] = Query(None, description="项目名称过滤操作"),
    project_id: Optional[str] = Query(None, description="项目ID"),
    project_id_op: Optional[str] = Query(None, description="项目ID过滤操作"),
    deadline_at: Optional[datetime] = Query(None, description="截止时间"),
    created_at_start: Optional[datetime] = Query(None, description="创建时间起始"),
    created_at_end: Optional[datetime] = Query(None, description="创建时间结束"),
    updated_at_start: Optional[datetime] = Query(None, description="更新时间起始"),
    updated_at_end: Optional[datetime] = Query(None, description="更新时间结束"),
    resolved_at_start: Optional[datetime] = Query(None, description="解决时间起始"),
    resolved_at_end: Optional[datetime] = Query(None, description="解决时间结束"),
    closed_at_start: Optional[datetime] = Query(None, description="关闭时间起始"),
    closed_at_end: Optional[datetime] = Query(None, description="关闭时间结束"),
    deadline_at_start: Optional[datetime] = Query(None, description="截止时间起始"),
    deadline_at_end: Optional[datetime] = Query(None, description="截止时间结束"),
    db: AsyncSession = Depends(get_db)
):
    try:
        priority_enum = TicketPriority(priority) if priority else None
        ticket_type_enum = TicketType(ticket_type) if ticket_type else None

        query_params = TicketQueryParams(
            page=page,
            size=size,
            id=id,
            id_op=id_op,
            title=title,
            title_op=title_op,
            status=status,
            priority=priority_enum,
            ticket_type=ticket_type_enum,
            created_by=created_by,
            created_by_op=created_by_op,
            created_by_name=created_by_name,
            assigned_to=assigned_to,
            assigned_to_op=assigned_to_op,
            assigned_to_name=assigned_to_name,
            customer=customer,
            customer_op=customer_op,
            customer_name=customer_name,
            related_resource_id=related_resource_id,
            related_resource_id_op=related_resource_id_op,
            keyword=keyword,
            tag=tag,
            project_name=project_name,
            project_name_op=project_name_op,
            project_id=project_id,
            project_id_op=project_id_op,
            deadline_at=deadline_at,
            created_at_start=created_at_start,
            created_at_end=created_at_end,
            updated_at_start=updated_at_start,
            updated_at_end=updated_at_end,
            resolved_at_start=resolved_at_start,
            resolved_at_end=resolved_at_end,
            closed_at_start=closed_at_start,
            closed_at_end=closed_at_end,
            deadline_at_start=deadline_at_start,
            deadline_at_end=deadline_at_end,
        )

        auth_header = request.headers.get("Authorization")
        token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

        result = await TicketService.get_tickets(db, query_params, token)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取工单列表失败: {str(e)}")


@router.post("/filter", response_model=TicketListResponse)
async def filter_tickets(
    filter_request: TicketFilterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    try:
        auth_header = request.headers.get("Authorization")
        token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

        result = await TicketService.filter_tickets(db, filter_request, token)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复合过滤查询工单列表失败: {str(e)}")


@router.get("/stats/overview", response_model=dict)
async def get_ticket_stats(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    try:
        stats = await TicketService.get_ticket_stats(db)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    request: Request,
    ticket_id: int,
    load_comments: bool = Query(False, description="是否加载评论"),
    db: AsyncSession = Depends(get_db)
):
    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

    ticket = await TicketService.get_ticket_by_id(db, ticket_id, load_comments, token)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单未找到")
    return ticket


@router.put("/{ticket_id}")
async def update_ticket(
    ticket_id: int,
    ticket_update: TicketUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    ticket = await TicketService.get_ticket_by_id(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单未找到")

    is_admin = getattr(current_user, 'is_admin', False)
    username = getattr(current_user, 'username', '')

    if not is_admin:
        if username not in [ticket.assigned_to, ticket.customer, ticket.created_by]:
            raise HTTPException(status_code=403, detail="无权限更新此工单")
        if ticket.status == TicketStatus.CLOSED:
            raise HTTPException(status_code=400, detail="已关闭的工单不能更新")
        if ticket_update.status:
            if ticket.status == TicketStatus.NEW and username != ticket.created_by:
                raise HTTPException(status_code=400, detail="只允许创建者开始工单！")
            if ticket.status in [TicketStatus.PENDING, TicketStatus.IN_PROGRESS] and username != ticket.assigned_to:
                raise HTTPException(status_code=400, detail="只允许处理人更新工单！")
            if ticket.status == TicketStatus.RESOLVED and username != ticket.customer:
                raise HTTPException(status_code=400, detail="只允许发起人的更新已解决工单！")

    try:
        token = getattr(current_user, 'token', None)
        result = await TicketService.update_ticket(db, ticket_id, ticket_update, token=token, operator_id=username)

        if result["ticket"] is None:
            raise HTTPException(status_code=404, detail="工单未找到")

        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=f"参数值错误: {str(ve)}")
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error updating ticket {ticket_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新工单失败: {str(e)}")


@router.delete("/{ticket_id}")
async def delete_ticket(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    ticket = await TicketService.get_ticket_by_id(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单未找到")

    is_admin = getattr(current_user, 'is_admin', False)
    username = getattr(current_user, 'username', '')

    if not is_admin:
        if username not in [ticket.assigned_to, ticket.created_by]:
            raise HTTPException(status_code=403, detail="无权限更新此工单")

    try:
        success = await TicketService.delete_ticket(db, ticket_id, is_admin)
        if not success:
            raise HTTPException(status_code=404, detail="工单未找到")
        return {"message": "工单删除成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除工单失败: {str(e)}")


@router.post("/{ticket_id}/comments", response_model=TicketCommentResponse)
async def add_comment(
    ticket_id: int,
    comment_data: TicketCommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    try:
        username = current_user.username if current_user else "system"
        comment = await TicketService.add_comment(db, ticket_id, comment_data, username, comment_attachment_map)
        if not comment:
            raise HTTPException(status_code=404, detail="工单未找到")

        from sqlalchemy import update
        from app.modules.fqa.ticket.models.ticket import Ticket
        from sqlalchemy.sql import func

        await db.execute(
            update(Ticket)
            .where(Ticket.id == ticket_id)
            .values(updated_at=func.now())
        )
        await db.commit()

        return comment
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加评论失败: {str(e)}")


@router.get("/{ticket_id}/comments", response_model=List[TicketCommentResponse])
async def get_ticket_comments(
    request: Request,
    ticket_id: int,
    db: AsyncSession = Depends(get_db)
):
    ticket = await TicketService.get_ticket_by_id(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单未找到")

    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

    comments = await TicketService.get_comments(db, ticket_id, token)
    return comments


@router.put("/comments/{comment_id}", response_model=TicketCommentResponse)
async def update_comment(
    comment_id: int,
    comment_update: TicketCommentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    from sqlalchemy import select
    from app.modules.fqa.ticket.models.ticket import TicketComment

    result = await db.execute(select(TicketComment).where(TicketComment.id == comment_id))
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="评论未找到")

    username = getattr(current_user, 'username', '')
    if comment.created_by != username:
        raise HTTPException(status_code=403, detail="无权限更新此评论")

    updated_comment = await TicketService.update_comment(db, comment_id, comment_update, comment_attachment_map)

    from sqlalchemy import update
    from app.modules.fqa.ticket.models.ticket import Ticket
    from sqlalchemy.sql import func

    await db.execute(
        update(Ticket)
        .where(Ticket.id == comment.ticket_id)
        .values(updated_at=func.now())
    )
    await db.commit()

    return updated_comment


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    from sqlalchemy import select
    from app.modules.fqa.ticket.models.ticket import TicketComment

    result = await db.execute(select(TicketComment).where(TicketComment.id == comment_id))
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="评论未找到")

    username = getattr(current_user, 'username', '')
    if comment.created_by != username:
        raise HTTPException(status_code=403, detail="无权限删除此评论")

    try:
        success = await TicketService.delete_comment(db, comment_id)
        if not success:
            raise HTTPException(status_code=404, detail="评论未找到")
        return {"message": "评论删除成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除评论失败: {str(e)}")


@router.patch("/{ticket_id}/status", response_model=TicketResponse)
async def update_ticket_status(
    ticket_id: int,
    status: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    ticket = await TicketService.get_ticket_by_id(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单未找到")

    is_admin = getattr(current_user, 'is_admin', False)
    username = getattr(current_user, 'username', '')

    if ticket.created_by != username and ticket.assigned_to != username and not is_admin:
        raise HTTPException(status_code=403, detail="无权限更新工单状态")

    try:
        status_enum = TicketStatus(status)
        updated_ticket = await TicketService.update_ticket_status(db, ticket_id, status_enum)
        return updated_ticket
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新工单状态失败: {str(e)}")


@router.patch("/{ticket_id}/assign", response_model=TicketResponse)
async def assign_ticket(
    ticket_id: int,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    is_admin = getattr(current_user, 'is_admin', False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="无权限分配工单")

    try:
        ticket = await TicketService.assign_ticket(db, ticket_id, user_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="工单未找到")
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分配工单失败: {str(e)}")


@router.post("/{ticket_id}/ai-assign")
async def trigger_ai_assignment(
    ticket_id: int,
    current_user = Depends(lambda: None)
):
    try:
        token = getattr(current_user, 'token', None)
        result = await TicketService.trigger_ai_assignment(ticket_id, token)
        if result.get("code") == 404:
            raise HTTPException(status_code=404, detail=result.get("message"))
        elif result.get("code") == 500:
            raise HTTPException(status_code=500, detail=result.get("message"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"触发AI分配处理人失败: {str(e)}")


@router.post("/comments/attachments")
async def upload_comment_attachment(
    file: UploadFile = File(...),
    temp_id: str = Form(...),
    current_user = Depends(lambda: None)
):
    try:
        file_bytes = await file.read()

        bucket_name = settings.COMMENT_BUCKET
        object_name = f"{temp_id}/{file.filename}"
        object_path = f"{bucket_name}/{object_name}"

        success = minio_client.upload_bytes(
            file_bytes=file_bytes,
            object_path=object_path,
            content_type=file.content_type
        )

        if not success:
            raise HTTPException(status_code=500, detail="上传附件失败")

        if temp_id not in comment_attachment_map:
            comment_attachment_map[temp_id] = []
        comment_attachment_map[temp_id].append(object_path)

        return {"message": "上传附件成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传附件失败: {str(e)}")


@router.post("/comments/attachments/delete")
async def delete_comment_attachment(
        temp_id: str = Form(...),
        file_name: str = Form(...),
        current_user = Depends(lambda: None)
):
    try:
        bucket_name = settings.COMMENT_BUCKET
        object_name = f"{temp_id}/{file_name}"
        object_path = f"{bucket_name}/{object_name}"

        if temp_id in comment_attachment_map:
            if object_path in comment_attachment_map[temp_id]:
                comment_attachment_map[temp_id].remove(object_path)
                if len(comment_attachment_map[temp_id]) == 0:
                    del comment_attachment_map[temp_id]

        success = minio_client.delete_file(object_path)

        if not success:
            raise HTTPException(status_code=500, detail="删除附件失败")

        return {"message": "删除附件成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除附件失败: {str(e)}")


@router.post("/cuiban-notification")
async def send_cuiban_notification(
    notification_data: TicketCuibanNotification,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(lambda: None)
):
    try:
        ticket_id = notification_data.ticket_id
        notify_type = notification_data.notify_type
        assigned_to = notification_data.assigned_to

        if ticket_id:
            ticket = await TicketService.get_ticket_by_id(db, ticket_id)
            if not ticket:
                raise HTTPException(status_code=404, detail="工单未找到")

            if not ticket.deadline_at:
                raise HTTPException(status_code=400, detail="工单未设置截止时间，无法发送催办通知")

            if not ticket.assigned_to:
                raise HTTPException(status_code=400, detail="工单未分配处理人，无法发送催办通知")

            user_names = [ticket.assigned_to]

            if notification_data.to_admin:
                user_names.extend(['wechat_oM1WF6jUTn', 'wechat_oM1WF6hHVK'])

            yuqi_day = ""
            if ticket.deadline_at:
                current_time = datetime.now()
                if current_time > ticket.deadline_at:
                    yuqi_seconds = (current_time - ticket.deadline_at).total_seconds()
                    yuqi_days = yuqi_seconds / (24 * 3600)
                    yuqi_day = f"{yuqi_days:.0f}"

            token = getattr(current_user, 'token', None)
            user_map = await TicketService._get_user_map(token)
            assigned_name = user_map.get(ticket.assigned_to, ticket.assigned_to)

            result = await NotificationUtils.send_ticket_cuiban_notification(
                ticket_id=ticket_id,
                notify_type=notify_type,
                project_name=ticket.project_name or "",
                ticket_name=ticket.title or "",
                assigned_name=assigned_name,
                deadline_at=ticket.deadline_at,
                create_at=ticket.created_at,
                user_names=user_names,
                token=token,
                yuqi_day=yuqi_day
            )

        else:
            extr = await TicketService.get_user_ticket_stats(db, assigned_to)
            token = getattr(current_user, 'token', None)
            result = await NotificationUtils.send_ticket_cuiban_notification(
                notify_type=notify_type,
                user_names=[assigned_to],
                extr=extr,
                token=token
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"发送催办通知失败: {str(e)}")