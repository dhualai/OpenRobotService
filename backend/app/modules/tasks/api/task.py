"""tasks 任务管理 API（承接 fqa/ticket）。

MIGRATION.md 阶段 3：从 `app/modules/fqa/ticket/api/ticket.py` 搬迁而来，
路由前缀从 `/api/fqa/tickets` 迁移到 `/api/tasks`。

Wave 2.2 完成：工单(tickets)已升格为任务(tasks)，本模块使用统一的 Task/TaskComment 模型。
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File, Form, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.database import get_async_db as get_db
from app.core.auth_routes import get_current_active_user_from_token
from app.modules.tasks.schemas.ticket import (
    TicketCreate, TicketUpdate, TicketResponse, TicketListResponse,
    TicketCommentCreate, TicketCommentUpdate, TicketCommentResponse,
    TicketQueryParams, TicketCuibanNotification, TicketFilterRequest
)
from app.modules.tasks.models.ticket import TicketStatus, TicketPriority, TicketType
from app.modules.tasks.services.ticket_service import TicketService
from app.utils.minio_client import minio_client
from app.utils.notification_utils import NotificationUtils
from app.core.config import settings

router = APIRouter(tags=["tasks"])

comment_attachment_map = {}


@router.post("/", response_model=TicketResponse)
async def create_task(
    ticket_data: TicketCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        username = current_user.get('username') if current_user else "system"
        token = current_user.get('token')
        logger.info(f"开始创建任务: title={ticket_data.title[:50] if ticket_data.title else '无标题'}, ticket_type={ticket_data.ticket_type}, created_by={username}")
        
        ticket = await TicketService.create_ticket(db, ticket_data, username, comment_attachment_map, token)
        logger.info(f"创建任务成功: task_id={ticket.id}, title={ticket.title[:50] if ticket.title else '无标题'}")
        return ticket
    except Exception as e:
        logger.error(f"创建任务失败: title={ticket_data.title[:50] if ticket_data.title else '无标题'}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建任务失败: {str(e)}")


@router.get("/", response_model=TicketListResponse)
async def get_tasks(
    request: Request,
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(10, ge=1, le=100, description="每页数量"),
    id: Optional[int] = Query(None, description="任务ID"),
    id_op: Optional[str] = Query(None, description="任务ID过滤操作：equals|gt|gte|lt|lte|ne"),
    title: Optional[str] = Query(None, description="任务标题"),
    title_op: Optional[str] = Query(None, description="标题过滤操作：equals|contains|notEquals"),
    status: Optional[str] = Query(None, description="任务状态，支持多个状态用逗号分隔"),
    priority: Optional[str] = Query(None, description="任务优先级"),
    ticket_type: Optional[str] = Query(None, description="任务类型"),
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
    source: Optional[str] = Query(None, description="任务来源"),
    source_op: Optional[str] = Query(None, description="来源过滤操作"),
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
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"开始获取任务列表, page={page}, size={size}, status={status}, keyword={keyword}, ticket_type={ticket_type}, priority={priority}")
        
        priority_enum = TicketPriority(priority) if priority else None
        ticket_type_enum = TicketType(ticket_type) if ticket_type else None
        logger.debug(f"优先级枚举转换完成, priority_enum={priority_enum}, ticket_type_enum={ticket_type_enum}")

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
            source=source,
            source_op=source_op,
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
        logger.debug(f"查询参数构建完成, query_params={query_params}")

        auth_header = request.headers.get("Authorization")
        token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None
        logger.debug(f"获取认证信息完成, has_token={token is not None}")

        result = await TicketService.get_tickets(db, query_params, token)
        logger.info(f"获取任务列表成功, total={result.get('total', 0)}, items_count={len(result.get('items', []))}")
        return result
    except Exception as e:
        logger.error(f"获取任务列表失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取任务列表失败: {str(e)}")


@router.post("/filter", response_model=TicketListResponse)
async def filter_tasks(
    filter_request: TicketFilterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"开始复合过滤查询任务列表, filters_count={len(filter_request.filters) if filter_request.filters else 0}, page={filter_request.page}, size={filter_request.size}")
        
        auth_header = request.headers.get("Authorization")
        token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

        result = await TicketService.filter_tickets(db, filter_request, token)
        logger.info(f"复合过滤查询任务列表成功, total={result.get('total', 0)}")
        return result
    except Exception as e:
        logger.error(f"复合过滤查询任务列表失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"复合过滤查询任务列表失败: {str(e)}")


@router.get("/stats/overview", response_model=dict)
async def get_task_stats(
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    try:
        stats = await TicketService.get_ticket_stats(db)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")


@router.get("/{task_id}", response_model=TicketResponse)
async def get_task(
    request: Request,
    task_id: int,
    load_comments: bool = Query(False, description="是否加载评论"),
    db: AsyncSession = Depends(get_db)
):
    import logging
    logger = logging.getLogger(__name__)
    
    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

    try:
        logger.info(f"开始获取任务详情: task_id={task_id}, load_comments={load_comments}")
        
        ticket = await TicketService.get_ticket_by_id(db, task_id, load_comments, token)
        if not ticket:
            logger.warning(f"任务未找到: task_id={task_id}")
            raise HTTPException(status_code=404, detail="任务未找到")
        logger.info(f"获取任务详情成功: task_id={task_id}, load_comments={load_comments}")
        return ticket
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务详情失败: task_id={task_id}, load_comments={load_comments}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取任务详情失败: {str(e)}")


@router.put("/{task_id}")
async def update_task(
    task_id: int,
    ticket_update: TicketUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = current_user.get('is_admin', False)
    username = current_user.get('username', '')

    if not is_admin:
        if username not in [ticket.assigned_to, ticket.customer, ticket.created_by]:
            raise HTTPException(status_code=403, detail="无权限更新此任务")
        if ticket.status == TicketStatus.CLOSED:
            raise HTTPException(status_code=400, detail="已关闭的任务不能更新")
        if ticket_update.status:
            if ticket.status == TicketStatus.NEW and username != ticket.created_by:
                raise HTTPException(status_code=400, detail="只允许创建者开始任务！")
            if ticket.status in [TicketStatus.PENDING, TicketStatus.IN_PROGRESS] and username != ticket.assigned_to:
                raise HTTPException(status_code=400, detail="只允许处理人更新任务！")
            if ticket.status == TicketStatus.RESOLVED and username != ticket.customer:
                raise HTTPException(status_code=400, detail="只允许发起人的更新已解决任务！")

    try:
        token = current_user.get('token')
        result = await TicketService.update_ticket(db, task_id, ticket_update, token=token, operator_id=username)

        if result["ticket"] is None:
            raise HTTPException(status_code=404, detail="任务未找到")

        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=f"参数值错误: {str(ve)}")
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error updating task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新任务失败: {str(e)}")


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = current_user.get('is_admin', False)
    username = current_user.get('username', '')

    if not is_admin:
        if username not in [ticket.assigned_to, ticket.created_by]:
            raise HTTPException(status_code=403, detail="无权限更新此任务")

    try:
        success = await TicketService.delete_ticket(db, task_id, is_admin)
        if not success:
            raise HTTPException(status_code=404, detail="任务未找到")
        return {"message": "任务删除成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除任务失败: {str(e)}")


@router.post("/{task_id}/comments", response_model=TicketCommentResponse)
async def add_comment(
    task_id: int,
    comment_data: TicketCommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    try:
        username = current_user.get('username') if current_user else "system"
        comment = await TicketService.add_comment(db, task_id, comment_data, username, comment_attachment_map)
        if not comment:
            raise HTTPException(status_code=404, detail="任务未找到")

        from sqlalchemy import update
        from app.modules.tasks.models.ticket import Ticket
        from sqlalchemy.sql import func

        await db.execute(
            update(Ticket)
            .where(Ticket.id == task_id)
            .values(updated_at=func.now())
        )
        await db.commit()

        return comment
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加评论失败: {str(e)}")


@router.get("/{task_id}/comments", response_model=List[TicketCommentResponse])
async def get_task_comments(
    request: Request,
    task_id: int,
    db: AsyncSession = Depends(get_db)
):
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

    comments = await TicketService.get_comments(db, task_id, token)
    return comments


@router.put("/comments/{comment_id}", response_model=TicketCommentResponse)
async def update_comment(
    comment_id: int,
    comment_update: TicketCommentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    from sqlalchemy import select
    from app.modules.tasks.models.ticket import TicketComment

    result = await db.execute(select(TicketComment).where(TicketComment.id == comment_id))
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="评论未找到")

    username = current_user.get('username', '')
    if comment.created_by != username:
        raise HTTPException(status_code=403, detail="无权限更新此评论")

    updated_comment = await TicketService.update_comment(db, comment_id, comment_update, comment_attachment_map)

    from sqlalchemy import update
    from app.modules.tasks.models.ticket import Ticket
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
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    from sqlalchemy import select
    from app.modules.tasks.models.ticket import TicketComment

    result = await db.execute(select(TicketComment).where(TicketComment.id == comment_id))
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="评论未找到")

    username = current_user.get('username', '')
    if comment.created_by != username:
        raise HTTPException(status_code=403, detail="无权限删除此评论")

    try:
        success = await TicketService.delete_comment(db, comment_id)
        if not success:
            raise HTTPException(status_code=404, detail="评论未找到")
        return {"message": "评论删除成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除评论失败: {str(e)}")


@router.patch("/{task_id}/status", response_model=TicketResponse)
async def update_task_status(
    task_id: int,
    status: str = Body(..., description="任务状态"),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = current_user.get('is_admin', False)
    username = current_user.get('username', '')

    if ticket.created_by != username and ticket.assigned_to != username and not is_admin:
        raise HTTPException(status_code=403, detail="无权限更新任务状态")

    try:
        status_enum = TicketStatus(status)
        updated_ticket = await TicketService.update_ticket_status(db, task_id, status_enum)
        return updated_ticket
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新任务状态失败: {str(e)}")


@router.patch("/{task_id}/assign", response_model=TicketResponse)
async def assign_task(
    task_id: int,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    is_admin = current_user.get('is_admin', False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="无权限分配任务")

    try:
        ticket = await TicketService.assign_ticket(db, task_id, user_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="任务未找到")
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分配任务失败: {str(e)}")


@router.post("/{task_id}/ai-assign")
async def trigger_ai_assignment(
    task_id: int,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    try:
        token = current_user.get('token')
        result = await TicketService.trigger_ai_assignment(task_id, token)
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
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
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
        current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
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
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    try:
        ticket_id = notification_data.ticket_id
        notify_type = notification_data.notify_type
        assigned_to = notification_data.assigned_to

        if ticket_id:
            ticket = await TicketService.get_ticket_by_id(db, ticket_id)
            if not ticket:
                raise HTTPException(status_code=404, detail="任务未找到")

            if not ticket.deadline_at:
                raise HTTPException(status_code=400, detail="任务未设置截止时间，无法发送催办通知")

            if not ticket.assigned_to:
                raise HTTPException(status_code=400, detail="任务未分配处理人，无法发送催办通知")

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

            token = current_user.get('token')
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
            token = current_user.get('token')
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