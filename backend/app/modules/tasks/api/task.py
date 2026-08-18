"""tasks 任务管理 API（承接 fqa/ticket）。

MIGRATION.md 阶段 3：从 `app/modules/fqa/ticket/api/ticket.py` 搬迁而来，
路由前缀从 `/api/fqa/tickets` 迁移到 `/api/tasks`。

Wave 2.2 完成：工单(tickets)已升格为任务(tasks)，本模块使用统一的 Task/TaskComment 模型。
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File, Form, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from app.core.database import get_async_db as get_db, db_manager
from app.core.auth_routes import get_current_active_user_from_token
from pydantic import BaseModel
from app.modules.tasks.schemas.ticket import (
    TicketCreate, TicketUpdate, TicketResponse, TicketListResponse,
    TicketCommentCreate, TicketCommentUpdate, TicketCommentResponse,
    TicketQueryParams, TicketCuibanNotification, TicketFilterRequest,
    TicketCreateNotificationRequest, ProjectMemberResponse
)
from app.modules.tasks.models.ticket import TicketStatus, TicketPriority, TicketType
from app.modules.tasks.services.ticket_service import TicketService
from app.modules.tasks.services.operation_log_service import OperationLogService, get_role_prefix
from app.models.task import OperationType
from app.modules.tasks.api.ws import ws_broadcast_comment, ws_broadcast_comment_deleted, ws_broadcast_task_updated, manager
from app.utils.minio_client import minio_client
from app.utils.notification_utils import NotificationUtils
from app.integrations.api import verify_sync_api_key
from app.core.config import settings

router = APIRouter(tags=["tasks"])

# 状态中文映射（用于操作日志描述）
STATUS_LABEL = {
    "new": "新建",
    "in_progress": "处理中",
    "pending": "待处理",
    "resolved": "已解决",
    "canceled": "已取消",
    "closed": "已关闭",
}

# 附件按扩展名分类（用于操作日志"添加了图片/视频/..."的描述）
_ATTACHMENT_EXT_CATEGORIES = {
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico", "tif", "tiff", "heic", "heif"},
    "video": {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "mpeg", "mpg", "3gp"},
    "audio": {"mp3", "wav", "aac", "flac", "ogg", "m4a", "wma", "aiff"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "tbz2"},
    "document": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv", "rtf", "odt", "ods", "odp"},
}
_ATTACHMENT_CATEGORY_LABEL = {
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "archive": "压缩包",
    "document": "文档",
    "other": "附件",
}


def _extract_filename(att) -> str:
    """从附件项提取文件名（兼容字符串路径或 dict 形式）。"""
    if isinstance(att, str):
        # 路径形如 "bucket/temp_id/filename.ext"
        return att.rsplit("/", 1)[-1]
    if isinstance(att, dict):
        return att.get("filename") or att.get("name") or str(att.get("url") or att.get("path") or "")
    return str(att)


def _categorize_attachment(filename: str) -> str:
    """根据文件名扩展名返回分类 key（image/video/audio/archive/document/other）。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for cat, exts in _ATTACHMENT_EXT_CATEGORIES.items():
        if ext in exts:
            return cat
    return "other"


def _get_attachment_label(attachments) -> Optional[str]:
    """根据 comment.attachments 列表返回中文类别标签；多类型混合时返回"附件"；无附件返回 None。"""
    if not attachments:
        return None
    categories = {_categorize_attachment(_extract_filename(a)) for a in attachments}
    if len(categories) == 1:
        return _ATTACHMENT_CATEGORY_LABEL[next(iter(categories))]
    return "附件"

# 解决方式总结 Worker 的 Redis 任务队列（与 ai/agents/AiTaskPlatform/services/resolution_worker.py 保持一致）
RESOLUTION_WORKER_QUEUE = "ors:resolution"
# 占位文案（前端 placeholder，不入库；这里用于识别"无内容"状态）
RESOLUTION_PLACEHOLDER_TEXT = "【请补充解决方法】"
RESOLUTION_PLACEHOLDER_ERROR = "【U老师自动总结出错了，请补充解决方法】"

comment_attachment_map = {}


async def _add_system_comment(db: AsyncSession, task_id: int, content: str, operator: str, token: str = ""):
    """向讨论区添加一条系统操作评论，并 WS 广播。失败不阻塞主流程。"""
    try:
        comment_data = TicketCommentCreate(content=content, is_public=True)
        comment = await TicketService.add_comment(db, task_id, comment_data, operator, comment_attachment_map, token=token)
        if comment:
            await db.commit()
            await db.refresh(comment)
            try:
                await ws_broadcast_comment("comment.created", task_id, comment)
            except Exception:
                pass
        return comment
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to add system comment for task {task_id}: {e}")
        return None


async def _reload_ticket_with_comments(db: AsyncSession, task_id: int):
    """重新查询工单（含 comments 关系）。

    _add_system_comment 内的 db.commit() 会使 session 中已加载的关系过期，
    导致 FastAPI 序列化响应时访问 ticket.comments 触发异步上下文外的懒加载（MissingGreenlet）。
    在调用 _add_system_comment 之后、返回响应之前调用此函数刷新工单。
    """
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from app.modules.tasks.models.ticket import Ticket
    result = await db.execute(
        select(Ticket).where(Ticket.id == task_id).options(joinedload(Ticket.comments))
    )
    return result.unique().scalar_one_or_none()


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
        
        # 记录创建操作日志
        user_name = current_user.get('name') or current_user.get('username') if current_user else None
        await OperationLogService.log(
            db=db,
            task_id=ticket.id,
            op_type=OperationType.CREATE,
            operator=username,
            operator_name=user_name,
            to_status=ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status),
            description=f"【创建人】{user_name or username} 创建了工单",
        )
        await OperationLogService.log(
            db=db,
            task_id=ticket.id,
            op_type=OperationType.STATUS_CHANGE,
            operator=username,
            operator_name=user_name,
            to_status=ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status),
            detail={"from": None, "to": ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status)},
            description=f"【创建人】{user_name or username} 将工单状态变更为「{ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status)}」",
        )
        
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
        
        # 记录查看操作日志（带5分钟去重）
        if token:
            try:
                from app.core.security import decode_token
                payload = decode_token(token)
                username = payload.get("sub") if payload else None
                if username:
                    user_name = payload.get("name", username) if payload else username
                    await OperationLogService.log_view(
                        db=db,
                        task_id=task_id,
                        username=username,
                        user_name=user_name,
                        ticket_created_by=getattr(ticket, 'created_by', None),
                        ticket_assigned_to=getattr(ticket, 'assigned_to', None),
                    )
            except Exception as view_err:
                logger.warning(f"Failed to log view for task {task_id}: {view_err}")
        
        return ticket
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务详情失败: task_id={task_id}, load_comments={load_comments}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取任务详情失败: {str(e)}")


@router.get("/{task_id}/similar", response_model=dict)
async def get_similar_tasks(
    task_id: int,
    limit: int = Query(10, description="返回相似工单条数上限"),
    db: AsyncSession = Depends(get_db),
):
    """@# 相似工单检索：按当前工单标题+描述做关键词相似，返回已解决的同款历史工单（含进行中? 否，限定 resolved）。

    仅用于 @# 引用"找相似"的弹列表（Q2d-①）。返回 [{task_id, title, status, project_name}]。
    跨项目、无权限过滤（工单可分享）；排除自身。
    """
    import logging
    from app.modules.tasks.models.ticket import Task, TaskStatus
    logger = logging.getLogger(__name__)
    try:
        # 读取当前工单文本作为查询基准
        cur = await db.get(Task, task_id)
        if not cur:
            raise HTTPException(status_code=404, detail="任务未找到")
        query_text = " ".join(filter(None, [cur.title or "", cur.description or ""]))

        # 关键词：过滤掉停用词/无意义单字，保留 2 字及以上 token
        import re as _re
        kws = set(_re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", query_text))
        kws.discard("问题")
        kws.discard("解决")
        if not kws:
            return {"task_id": task_id, "similar": []}

        # 限定已解决，排除自身；按标题+描述匹配关键词打分（命中数加权）
        from sqlalchemy import or_, select

        # 简单打分：标题命中权重高于描述，用关键词出现次数近似
        conditions = []
        for kw in kws:
            pat = f"%{kw}%"
            conditions.append(Task.title.ilike(pat))
            conditions.append(Task.description.ilike(pat))
        # distinct 去重并按创建时间倒序取前 N（打分近似：先取含任一关键词的候选，再按更新排序）
        stmt = (
            select(Task)
            .where(Task.status == TaskStatus.RESOLVED)
            .where(Task.id != task_id)
            .where(or_(*conditions))
            .order_by(Task.created_at.desc())
            .limit(limit * 3)  # 多取一些用于打分
        )
        rows = (await db.execute(stmt)).scalars().all()

        # 打分：标题命中 +3/词，描述命中 +1/词
        scored = []
        for t in rows:
            title = t.title or ""
            desc = t.description or ""
            score = 0
            for kw in kws:
                if kw in title:
                    score += 3
                if kw in desc:
                    score += 1
            scored.append((score, t))

        scored.sort(key=lambda x: (-x[0], (x[1].created_at or datetime.min)))
        similar = []
        for score, t in scored[:limit]:
            if score <= 0:
                continue
            similar.append({
                "task_id": t.id,
                "title": (t.title or "")[:80] or f"工单#{t.id}",
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "project_name": getattr(t, "project_name", "") or "",
            })
        return {"task_id": task_id, "similar": similar}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"相似工单检索失败: task_id={task_id}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"相似工单检索失败: {str(e)}")


@router.get("/{task_id}/project-members", response_model=List[ProjectMemberResponse])
async def get_task_project_members(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    """获取任务关联项目的成员列表 + 工单处理人（用于讨论区 @ 提及）。"""
    import logging
    logger = logging.getLogger(__name__)

    try:
        ticket = await TicketService.get_ticket_by_id(db, task_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="任务未找到")

        result = []
        seen = set()

        project_id = getattr(ticket, "project_id", None)

        # ── 1. 提单人和被指派人始终排在最前面 ──
        key_users = []
        assigned_to = getattr(ticket, "assigned_to", None)
        created_by = getattr(ticket, "created_by", None)
        if assigned_to:
            key_users.append((assigned_to, "处理人"))
        if created_by and created_by != assigned_to:
            key_users.append((created_by, "提单人"))

        if key_users:
            from app.core.db import SessionLocal
            from app.models.identity import UserDB
            sync_db = SessionLocal()
            try:
                for uid_or_username, role_label in key_users:
                    user = sync_db.query(UserDB).filter(
                        (UserDB.username == uid_or_username) | (UserDB.id == uid_or_username)
                    ).first()
                    if user:
                        uname = user.username or ""
                        if uname and uname not in seen:
                            seen.add(uname)
                            result.append(ProjectMemberResponse(
                                id=uname,
                                username=uname,
                                name=user.name or uname,
                                role_name=role_label,
                            ))
            finally:
                sync_db.close()

        # ── 2. 提单人/处理人 + 项目成员 ──
        # 即使没有项目也能 @ 提单人和处理人
        if project_id:
            members = db_manager.get_project_members(project_id, include_usp=False)
            for m in members:
                uname = (m.get("username") or "").strip()
                if not uname or uname in seen:
                    continue
                seen.add(uname)
                name = m.get("name")
                result.append(ProjectMemberResponse(
                    id=uname,
                    username=uname,
                    name=name if name else uname,
                    role_name=m.get("role_name"),
                ))

        # 即使没有项目也能 @ 处理人
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取项目成员失败: task_id={task_id}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取项目成员失败: {str(e)}")


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
    user_name = current_user.get('name', username)

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
        # ── WS 实时广播：工单字段更新（标题/描述/处理人等）──
        try:
            t = result.get("ticket")
            if t:
                await ws_broadcast_task_updated(task_id, t)
        except Exception:
            pass

        if result["ticket"] is None:
            raise HTTPException(status_code=404, detail="任务未找到")

        # ── 记录操作日志 ──
        token = current_user.get('token') or ''
        _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username)
        # 1. 状态变更日志 + 系统评论
        if ticket_update.status:
            new_status = ticket_update.status.value if hasattr(ticket_update.status, 'value') else str(ticket_update.status)
            old_status = ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status)
            await OperationLogService.log(
                db=db,
                task_id=task_id,
                op_type=OperationType.STATUS_CHANGE,
                operator=username,
                operator_name=user_name,
                to_status=new_status,
                detail={"from": old_status, "to": new_status},
                description=f"{_role}{user_name} 将工单状态变更为「{new_status}」" if _role else f"{user_name} 将工单状态变更为「{new_status}」",
            )
            await _add_system_comment(
                db, task_id,
                f"{user_name} 将工单状态变更为「{STATUS_LABEL.get(new_status, new_status)}」",
                username, token,
            )
        
        # 2. 其他操作日志（根据 operation_type 或字段变更推断）
        op_type_str = ticket_update.operation_type
        changed_fields = []
        update_data = ticket_update.model_dump(exclude={'operation_type'}, exclude_unset=True)
        for key, value in update_data.items():
            if value is not None and key != 'status':
                changed_fields.append(key)
        
        if op_type_str == 'escalate':
            await OperationLogService.log(
                db=db, task_id=task_id, op_type=OperationType.ESCALATE,
                operator=username, operator_name=user_name,
                description=f"{_role}{user_name} 升级了工单" if _role else f"{user_name} 升级了工单",
            )
            await _add_system_comment(db, task_id, f"{user_name} 升级了工单", username, token)
        elif op_type_str == 'return':
            await OperationLogService.log(
                db=db, task_id=task_id, op_type=OperationType.RETURN,
                operator=username, operator_name=user_name,
                description=f"{_role}{user_name} 退回了工单" if _role else f"{user_name} 退回了工单",
            )
            await _add_system_comment(db, task_id, f"{user_name} 退回了工单", username, token)
        elif op_type_str == 'reassign':
            new_assignee = update_data.get('assigned_to', '')
            user_map = await TicketService._get_user_map(token)
            new_assignee_name = user_map.get(new_assignee, new_assignee)
            await OperationLogService.log(
                db=db, task_id=task_id, op_type=OperationType.REASSIGN,
                operator=username, operator_name=user_name,
                detail={"new_assignee": new_assignee},
                description=f"{_role}{user_name} 将工单重新指派给 {new_assignee_name}" if _role else f"{user_name} 将工单重新指派给 {new_assignee_name}",
            )
            await _add_system_comment(db, task_id, f"{user_name} 将工单重新指派给 {new_assignee_name}", username, token)
            # 工单转派提醒：通知创建人 + 新被指派人
            reassign_notify_users = list({ticket.created_by, new_assignee} - {None})
            await NotificationUtils.send_ticket_reassign_notification(
                ticket_id=task_id,
                title=ticket.title or '',
                project_name=ticket.project_name or '',
                operator=user_name,
                new_assignee=new_assignee_name,
                deadline_at=ticket.deadline_at,
                user_names=reassign_notify_users,
                token=token,
            )
        elif changed_fields:
            # 普通字段更新
            field_labels = {
                'title': '标题', 'description': '描述', 'priority': '优先级',
                'ticket_type': '类型', 'customer': '客户', 'team': '团队',
                'project_name': '项目名称', 'project_id': '项目ID',
            }
            label_list = [field_labels.get(f, f) for f in changed_fields]
            await OperationLogService.log(
                db=db, task_id=task_id, op_type=OperationType.UPDATE,
                operator=username, operator_name=user_name,
                detail={"fields": changed_fields},
                description=f"{_role}{user_name} 修改了工单的「{'、'.join(label_list)}」" if _role else f"{user_name} 修改了工单的「{'、'.join(label_list)}」",
            )

        # _add_system_comment 的 commit 会使 result["ticket"] 的 comments 关系过期，
        # 需重新查询以避免 FastAPI 序列化时触发异步外的懒加载（MissingGreenlet）
        result["ticket"] = await _reload_ticket_with_comments(db, task_id)
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
        operator = current_user.get('name') or username
        comment = await TicketService.add_comment(db, task_id, comment_data, username, comment_attachment_map, token=current_user.get("token"))
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

        # ── 记录评论操作日志 ──
        # 区分三种场景：仅附件 / 附件+文字 / 仅文字（含空内容兜底）
        # 附件类型按扩展名识别：图片/视频/音频/压缩包/文档；多类型混合用"附件"
        content_text = (comment_data.content or '').strip()
        content_summary = content_text[:100] + ('...' if len(content_text) > 100 else '')
        cat_label = _get_attachment_label(getattr(comment, 'attachments', None))

        if cat_label and not content_text:
            action_text = f"添加了{cat_label}"
        elif cat_label and content_text:
            action_text = f"添加了{cat_label}并附带评论 {content_summary}"
        else:
            action_text = f"添加了评论：{content_summary}"

        # 获取工单信息用于角色判断
        _ticket = await TicketService.get_ticket_by_id(db, task_id)
        _role = get_role_prefix(getattr(_ticket, 'created_by', None), getattr(_ticket, 'assigned_to', None), username) if _ticket else ""
        await OperationLogService.log(
            db=db,
            task_id=task_id,
            op_type=OperationType.COMMENT,
            operator=username,
            operator_name=operator,
            description=f"{_role}{operator} {action_text}" if _role else f"{operator} {action_text}",
        )

        # ── @mention 通知：检测评论中的 @用户名，排除 @U老师 ──
        _maybe_notify_mentions(
            task_id=task_id, content=comment_data.content,
            operator=operator, token=current_user.get("token"),
        )

        # ── WS 实时广播：评论创建（失败不影响主流程）──
        try:
            await ws_broadcast_comment("comment.created", task_id, comment)
        except Exception:
            pass

        return comment
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加评论失败: {str(e)}")


def _maybe_notify_mentions(
    task_id: int, content: str, operator: str, token: Optional[str] = None,
):
    """检测评论中 @ 的用户名（排除 AI），发送通知"""
    import re
    import logging

    ai_names = {"U老师", "小U", "AI助手"}
    mentioned = set()
    # 排除 "@#编号" 工单引用（@# 后跟的纯数字是工单号，不是用户名），
    # 避免把工单引用误判成对数字用户名发通知。
    for m in re.finditer(r"@(?!\d)([\w一-鿿]+)", content):
        name = m.group(1)
        if name not in ai_names:
            mentioned.add(name)
    if not mentioned:
        return

    # 查本地 users 表解析 username
    from app.core.db import SessionLocal
    from app.models.identity import UserDB
    from app.models.task import Task  # 同步 ORM 模型（非异步 session）

    db = SessionLocal()
    try:
        ticket = db.query(Task).filter(Task.id == task_id).first()
        if not ticket:
            return
        ticket_title = ticket.title or ""
        ticket_project = ticket.project_name or ""

        # 按 @内容 匹配用户 → 先按 name 查，再按 username 查
        notified_usernames = []
        for mentioned_name in mentioned:
            # 先按中文名匹配
            user = db.query(UserDB).filter(UserDB.name == mentioned_name).first()
            if not user:
                # 回退按 username 匹配（前端可能插入的是 @username）
                user = db.query(UserDB).filter(UserDB.username == mentioned_name).first()
            if user:
                if user.username not in notified_usernames:
                    notified_usernames.append(user.username)

        if not notified_usernames:
            return

        logger = logging.getLogger(__name__)
        logger.info(
            f"@mention 通知: task_id={task_id}, operator={operator}, "
            f"mentioned={list(mentioned)}, notified={notified_usernames}"
        )

        import asyncio

        async def _notify():
            try:
                # 取工单真实状态的中文名
                status_text_map = {
                    "new": "新建", "in_progress": "处理中", "pending": "待处理",
                    "resolved": "已解决", "closed": "已关闭", "canceled": "已取消",
                }
                raw_status = (ticket.status.value if hasattr(ticket.status, 'value')
                              else str(ticket.status or "")).lower()
                status_text = status_text_map.get(raw_status, raw_status)

                deadline_str = (ticket.deadline_at.strftime('%Y-%m-%d %H:%M:%S')
                                if ticket.deadline_at
                                else (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S'))
                payload = NotificationUtils.instantiate_template(
                    NotificationUtils.MENTION_TICKET,
                    ticket_title[:20] or f"工单#{task_id}",
                    (ticket_project or "未关联项目")[:20],
                    status_text,
                    f"{operator} 在工单中@了您",
                    deadline_str,
                    user_names=notified_usernames,
                    url=NotificationUtils.TICKET_HOST + f"/{task_id}",
                )
                await NotificationUtils.send_notification(payload, token)
            except Exception as e:
                import logging
                _logger = logging.getLogger(__name__)
                _logger.error(f"@mention 通知发送异常: {e}")

        asyncio.create_task(_notify())
    finally:
        db.close()


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

    # ── WS 实时广播：评论编辑 ──
    try:
        await ws_broadcast_comment("comment.updated", comment.ticket_id, updated_comment)
    except Exception:
        pass

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
        # ── WS 实时广播：评论删除 ──
        try:
            await ws_broadcast_comment_deleted(comment.ticket_id, comment_id)
        except Exception:
            pass
        return {"message": "评论删除成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除评论失败: {str(e)}")


@router.patch("/{task_id}/status", response_model=TicketResponse)
async def update_task_status(
    task_id: int,
    status: str = Body(..., embed=True, description="任务状态"),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    request: Request = None
):
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = current_user.get('is_admin', False)
    username = current_user.get('username', '')
    token = request.headers.get("Authorization", "").replace("Bearer ", "") if request else ""

    # AI 工单（source='ai'）允许任何登录用户操作状态（created_by='system' 不是真实用户）
    if ticket.source == 'ai':
        pass
    elif ticket.created_by != username and ticket.assigned_to != username and not is_admin:
        raise HTTPException(status_code=403, detail="无权限更新任务状态")

    try:
        status_enum = TicketStatus(status)

        # ── 结束工单（→ resolved）需携带解决方式：接单人确认后提交的最终文本 ──
        resolution_summary = None
        try:
            if request:
                body = await request.json()
                resolution_summary = (body or {}).get("resolution_summary")
        except Exception:
            resolution_summary = None

        if status_enum == TicketStatus.RESOLVED:
            # 必填校验：去空白后非空（占位提示由前端 placeholder 控制，不入值）
            rs = (resolution_summary or "").strip()
            if not rs:
                raise HTTPException(status_code=400, detail="结束工单必须填写解决方式")
            resolution_summary = rs

        old_status = ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status)
        updated_ticket = await TicketService.update_ticket_status(db, task_id, status_enum, token=token, operator_id=username, resolution_summary=resolution_summary)
        # ── WS 实时广播：工单状态变更 ──
        try:
            await ws_broadcast_task_updated(task_id, updated_ticket)
        except Exception:
            pass

        # ── 记录状态变更操作日志 ──
        user_name = current_user.get('name', username)
        _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username)
        await OperationLogService.log(
            db=db,
            task_id=task_id,
            op_type=OperationType.STATUS_CHANGE,
            operator=username,
            operator_name=user_name,
            to_status=status,
            detail={"from": old_status, "to": status},
            description=f"{_role}{user_name} 将工单状态变更为「{STATUS_LABEL.get(status, status)}」" if _role else f"{user_name} 将工单状态变更为「{STATUS_LABEL.get(status, status)}」",
        )

        # ── 向讨论区添加系统评论 ──
        await _add_system_comment(
            db, task_id,
            f"{user_name} 将工单状态变更为「{STATUS_LABEL.get(status, status)}」",
            username, token,
        )

        # _add_system_comment 的 commit 会使 updated_ticket 的 comments 关系过期，
        # 需重新查询以避免 FastAPI 序列化时触发异步外的懒加载（MissingGreenlet）
        return await _reload_ticket_with_comments(db, task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新任务状态失败: {str(e)}")


@router.post("/{task_id}/resolution-summary")
async def get_resolution_summary(
    task_id: int,
    force: bool = Body(False, embed=True, description="强制重新入队生成（重试场景）"),
    clear: bool = Body(False, embed=True, description="清除已保存的解决方式草稿与生成状态（接单人取消时调用）"),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """结束工单确认弹窗：获取工单问题 + AI 解决方式草案。

    - 若 clear=true → 仅清除已保存的 resolution_summary 与生成状态（不入队不生成），供"取消"使用。
    - 若 metadata_info.resolution_summary 已有（worker 生成的草案或已确认值）→ 直接返回。
    - 若 force=true → 视为重试，清掉"无内容"标记并重新入队生成。
    - 若无 → 把任务 LPUSH 到 Redis 队列，由 ai 侧 resolution worker 异步生成（前端轮询回读）。
    """
    import logging
    _logger = logging.getLogger(__name__)
    _user = (current_user or {}).get('username', '?')
    _logger.info(f"[resolution-summary] 接口被调用: task_id={task_id}, force={force}, clear={clear}, user={_user}")

    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        _logger.warning(f"[resolution-summary] task_id={task_id} 不存在 (404)")
        raise HTTPException(status_code=404, detail="任务未找到")

    meta = ticket.metadata_info or {}

    # 取消：清除已保存的解决方式草稿与生成状态（不重新生成，下次点击再生成）
    if clear:
        # 复制新 dict 再赋回，强制 SQLAlchemy 检测 JSON 列变化（原地 pop 可能不触发 UPDATE）
        new_meta = dict(meta)
        new_meta.pop("resolution_summary", None)
        new_meta.pop("resolution_summary_at", None)
        new_meta.pop("resolution_gen_state", None)
        new_meta.pop("resolution_requested_at", None)
        new_meta.pop("resolution_empty_at", None)
        new_meta.pop("resolution_status", None)  # 清理历史遗留的旧字段名残留
        ticket.metadata_info = new_meta
        await db.commit()
        _logger.debug(f"[resolution-summary] task_id={task_id} clear=true 已清除解决方式草稿与生成状态, 剩余 keys={list(new_meta.keys())}")
        return {
            "task_id": task_id,
            "problem": {"title": ticket.title or "", "description": ticket.description or ""},
            "resolution_summary": "",
            "has_ai": False,
            "status": "cleared",
        }

    _logger.debug(f"[resolution-summary] task_id={task_id} metadata keys={list(meta.keys())}, resolution_gen_state={meta.get('resolution_gen_state')}, has_summary={bool(meta.get('resolution_summary'))}")

    # 强制重试：清掉"无内容(done)"标记，允许重新入队
    if force:
        if meta.get("resolution_gen_state") in ("done", "empty") and not meta.get("resolution_summary"):
            meta.pop("resolution_gen_state", None)
            _m = dict(meta)
            ticket.metadata_info = _m
            await db.commit()
            meta = _m
            _logger.debug(f"[resolution-summary] task_id={task_id} force=true 已清除无内容标记，放行重新入队")

    # 已有解决方式（草案/已确认）→ 直接返回
    if meta.get("resolution_summary"):
        _logger.debug(f"[resolution-summary] task_id={task_id} 命中已有解决方式，直接返回 (status=done)")
        return {
            "task_id": task_id,
            "problem": {"title": ticket.title or "", "description": ticket.description or ""},
            "resolution_summary": meta["resolution_summary"],
            "has_ai": True,
            "status": "done",
        }

    # 状态 done/empty 但无内容（worker 曾判定无材料）→ 非 force 时直接返回空，不再重复入队
    # （有内容的 done 已在上面命中 resolution_summary 分支返回，不会走到这里）
    # 只有 force=true（用户主动重试）时才会清除状态走下面的重新入队分支。
    if meta.get("resolution_gen_state") == "empty":
        _logger.debug(f"[resolution-summary] task_id={task_id} 生成状态 empty（无材料），直接返回 (status=empty)，不重复入队")
        return {
            "task_id": task_id,
            "problem": {"title": ticket.title or "", "description": ticket.description or ""},
            "resolution_summary": "",
            "has_ai": False,
            "status": "empty",
        }

    # 已在生成中（此前已入队，worker 正在异步总结）→ 只读返回，不重复入队
    if meta.get("resolution_gen_state") == "pending":
        _logger.debug(f"[resolution-summary] task_id={task_id} 生成状态 pending（生成中），返回空 (status=pending)")
        return {
            "task_id": task_id,
            "problem": {"title": ticket.title or "", "description": ticket.description or ""},
            "resolution_summary": "",
            "has_ai": False,
            "status": "pending",
        }

    # 状态 done 且无内容（历史遗留，非 empty）→ 放行重新入队一次（兼容旧数据）
    if meta.get("resolution_gen_state") == "done":
        _logger.debug(f"[resolution-summary] task_id={task_id} 生成状态 done 但无内容，放行重新入队")

    # 无解决方式且未在生成 → 触发 ai worker 异步生成（LPUSH 到 Redis 队列）
    from datetime import datetime
    enqueue_status = "pending"
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
        )
        try:
            await r.lpush(RESOLUTION_WORKER_QUEUE, str(int(task_id)))
            meta["resolution_gen_state"] = "pending"
            meta["resolution_requested_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ticket.metadata_info = meta
            await db.commit()
            _logger.info(f"[resolution-summary] task_id={task_id} 已入队到 {RESOLUTION_WORKER_QUEUE} 触发 worker 生成")
        finally:
            await r.aclose()
    except Exception as e:
        # 入队失败不影响弹窗；前端 placeholder 兜底提示
        enqueue_status = "failed"
        _logger.error(f"[resolution-summary] task_id={task_id} 入队失败: {e}")

    _logger.info(f"[resolution-summary] task_id={task_id} 返回 enqueue_status={enqueue_status}")
    return {
        "task_id": task_id,
        "problem": {"title": ticket.title or "", "description": ticket.description or ""},
        "resolution_summary": "",
        "has_ai": False,
        "status": enqueue_status,
    }


@router.patch("/{task_id}/assign", response_model=TicketResponse)
async def assign_task(
    task_id: int,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    # 放开 admin 限制：允许任何已登录用户改派（兜底双工单场景下提单人需将工单派给项目负责人）。
    try:
        username = current_user.get('username', 'system')
        user_name = current_user.get('name', username)
        token = current_user.get('token')

        ticket = await TicketService.assign_ticket(db, task_id, user_id)
        # ── WS 实时广播：工单改派 ──
        try:
            await ws_broadcast_task_updated(task_id, ticket)
        except Exception:
            pass
        if not ticket:
            raise HTTPException(status_code=404, detail="任务未找到")

        # ── 记录改派操作日志 ──
        user_map = await TicketService._get_user_map(token)
        assignee_name = user_map.get(user_id, user_id)
        _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username) if ticket else ""
        await OperationLogService.log(
            db=db,
            task_id=task_id,
            op_type=OperationType.ASSIGN,
            operator=username,
            operator_name=user_name,
            detail={"new_assignee": user_id},
            description=f"{_role}{user_name} 将工单指派给 {assignee_name}" if _role else f"{user_name} 将工单指派给 {assignee_name}",
        )

        # ── 向讨论区添加系统评论 ──
        await _add_system_comment(db, task_id, f"{user_name} 将工单指派给 {assignee_name}", username, token)

        # 工单转派提醒：通知创建人 + 新被指派人
        assign_notify_users = list({ticket.created_by, user_id} - {None})
        await NotificationUtils.send_ticket_reassign_notification(
            ticket_id=task_id,
            title=ticket.title or '',
            project_name=ticket.project_name or '',
            operator=user_name,
            new_assignee=assignee_name,
            deadline_at=ticket.deadline_at,
            user_names=assign_notify_users,
            token=token,
        )

        # _add_system_comment 的 commit 会使 ticket 的 comments 关系过期，
        # 需重新查询以避免 FastAPI 序列化时触发异步外的懒加载（MissingGreenlet）
        return await _reload_ticket_with_comments(db, task_id)
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


class ReDispatchRequest(BaseModel):
    """重新派单请求体。preferred_assignee 为用户倾向的派单人（username/userId，必填）；remark 为可选备注。"""
    preferred_assignee: str
    remark: Optional[str] = None


@router.post("/{task_id}/re-dispatch", response_model=TicketResponse)
async def re_dispatch_task(
    task_id: int,
    payload: ReDispatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """重新派单：强制工单回到待派单状态，触发 AI 智能派单重新推荐处理人。

    - 可携带用户倾向的派单人（preferred_assignee，username），派单流水线会将其作为强加权信号
      （复用 assigner 既有的 preferred_assignee 字段，见 TicketContext.preferred_assignee）。
    - 实现：清空 assigned_to + 状态回 new + 写入 metadata_info.preferred_assignee，
      再向 Redis 发布 usp:new_ticket 事件，由派单 Worker 立即重新派单（发布失败则依赖定时扫描兜底）。
    """
    import logging
    logger = logging.getLogger(__name__)

    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = current_user.get('is_admin', False)
    username = current_user.get('username', '')
    user_name = current_user.get('name', username)
    token = current_user.get('token')

    # 权限口径对齐 update_task：管理员 / 提单人 / 处理人 / 客户
    if not is_admin and username not in [ticket.assigned_to, ticket.customer, ticket.created_by]:
        raise HTTPException(status_code=403, detail="无权限重新派单此任务")
    if ticket.status == TicketStatus.CLOSED:
        raise HTTPException(status_code=400, detail="已关闭的任务不能重新派单")
    # 派单 Worker 只处理 source='ai' 的工单（manual 系统任务由兜底双工单直接指定处理人，不走 AI 派单），
    # 若允许 manual 工单重派，Worker 永远查不到它，会一直卡在「派单中」。
    if (ticket.source or "") != "ai":
        raise HTTPException(status_code=400, detail="该工单非智能派单工单，无法重新派单")
    # 提单时已指定处理人（title/description 里的强信号）会触发派单 Step 0 直接指派，
    # 覆盖掉重新派单的倾向人，导致重派无效——提前拦截并提示（正则与 assigner Step 0 口径一致）。
    import re as _re
    _strong_text = f"{ticket.title or ''}\n{ticket.description or ''}"
    _strong_m = _re.search(r"指定(?:处理人|人|人员)[:：]\s*([^\]\s，,；;:：）)】]{2,6})", _strong_text)
    if _strong_m:
        raise HTTPException(
            status_code=400,
            detail=f"该工单已指定处理人「{_strong_m.group(1).strip()}」，重新派单不会改变接单人",
        )

    preferred = (payload.preferred_assignee or "").strip()
    if not preferred:
        raise HTTPException(status_code=400, detail="请选择倾向处理人")
    remark = (payload.remark or "").strip()

    # 复位前捕获旧值，供操作日志角色判定（复位后 assigned_to 已清空）
    created_by = ticket.created_by
    old_assigned_to = ticket.assigned_to

    # 重置派单状态：清空处理人 + 状态回 new
    ticket.assigned_to = None
    ticket.status = TicketStatus.NEW

    # 写入用户倾向派单人 + 清掉上一次派单的推荐元数据
    meta = dict(ticket.metadata_info or {})
    meta["preferred_assignee"] = preferred
    if remark:
        meta["preferred_assignee_remark"] = remark
    for k in ("assignee_name", "assignee_username", "assign_confidence",
              "assign_reasoning", "assign_decision_type", "assigned_at"):
        meta.pop(k, None)
    ticket.metadata_info = meta

    await db.commit()

    # 触发派单 Worker：向 Redis 发布 usp:new_ticket（与 AI 服务 publish_new_ticket 同通道）
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
        )
        try:
            await r.publish("usp:new_ticket", str(int(task_id)))
        finally:
            await r.aclose()
    except Exception as e:
        logger.warning(f"重新派单发布 Redis 事件失败（将依赖定时扫描兜底）: {e}")

    # 操作日志 + 系统评论
    _role = get_role_prefix(created_by, old_assigned_to, username)
    user_map = await TicketService._get_user_map(token)
    pref_name = user_map.get(preferred, preferred)
    base = f"重新派单，倾向处理人 {pref_name}"
    desc = f"{_role}{user_name} {base}" if _role else f"{user_name} {base}"
    comment_text = f"{user_name} {base}"
    if remark:
        comment_text += f"（备注：{remark}）"
    await OperationLogService.log(
        db=db,
        task_id=task_id,
        op_type=OperationType.REASSIGN,
        operator=username,
        operator_name=user_name,
        detail={"preferred_assignee": preferred, "remark": remark or None},
        description=desc,
    )
    await _add_system_comment(db, task_id, comment_text, username, token)

    return await _reload_ticket_with_comments(db, task_id)


@router.get("/{task_id}/operation-logs", response_model=List[dict])
async def get_task_operation_logs(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    """获取工单操作日志列表（按时间倒序）"""
    try:
        logs = await OperationLogService.list_by_task(db, task_id)

        # 转换为前端需要的格式
        result = []
        for log in logs:
            result.append({
                "id": log.id,
                "task_id": log.task_id,
                "operation_type": log.operation_type.value if hasattr(log.operation_type, 'value') else str(log.operation_type),
                "operator": log.operator,
                "operator_name": log.operator_name,
                "to_status": log.to_status,
                "detail": log.detail,
                "description": log.description,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "ended_at": log.ended_at.isoformat() if log.ended_at else None,
                "duration_seconds": log.duration_seconds,
            })
        return result
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"获取工单操作日志失败: task_id={task_id}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取工单操作日志失败: {str(e)}")


class ViewEndRequest(BaseModel):
    duration_seconds: int


@router.post("/{task_id}/view-end")
async def report_view_duration(
    task_id: int,
    payload: ViewEndRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """回传用户查看工单的停留时长。

    前端在用户离开页面（pagehide / visibilitychange→hidden / 组件卸载）时调用，
    将累计的可见停留秒数回传给后端，后端累加到最近一条 VIEW 操作记录上。
    使用 JWT 中的 sub 作为操作人标识，与查看记录创建时的去重逻辑一致。
    """
    import logging
    logger = logging.getLogger(__name__)

    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="未授权")

    try:
        from app.core.security import decode_token
        payload_jwt = decode_token(token)
        username = payload_jwt.get("sub") if payload_jwt else None
        if not username:
            raise HTTPException(status_code=401, detail="无效的令牌")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"view-end token decode failed: {e}")
        raise HTTPException(status_code=401, detail="令牌解析失败")

    duration = int(payload.duration_seconds or 0)
    ok = await OperationLogService.update_view_duration(
        db=db,
        task_id=task_id,
        username=username,
        duration_seconds=duration,
    )
    return {"ok": ok, "duration_seconds": duration}


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
        import logging
        logging.getLogger(__name__).error(
            f"[attach-upload] 上传附件失败 temp_id={temp_id} filename={getattr(file, 'filename', '?')} bucket={settings.COMMENT_BUCKET}",
            exc_info=True,
        )
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

            # 通知目标：优先用前端传的 assigned_to（用户选择），其次用工单的 assigned_to
            target_user = notification_data.assigned_to or ticket.assigned_to
            if not target_user:
                raise HTTPException(status_code=400, detail="请选择通知对象")

            user_names = [target_user]

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
            assigned_name = user_map.get(target_user, target_user)

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


@router.post("/ticket-create-notification")
async def send_ticket_create_notification(
    body: TicketCreateNotificationRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_sync_api_key),
):
    """新建工单通知（内部接口，供 AI 派单服务调用）。

    调用方仅传入 task_id（+ 可选 operator），后端按 task_id 查询完整工单后，
    组装标题/项目/截止时间/受理人等字段，向受理人发起「新建工单」通知。
    鉴权走 X-API-Key（与用户 JWT 分离），需与后端 HELPDESK_SYNC_API_KEY 一致。
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        ticket = await TicketService.get_ticket_by_id(db, body.task_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="工单不存在")
        if not ticket.assigned_to:
            raise HTTPException(status_code=400, detail="工单尚未指派受理人，无法发送新建通知")

        # 派单人 = 工单创建人（发起人），从 created_by 转换为用户名
        user_map = await TicketService._get_user_map(None)
        operator = user_map.get(ticket.created_by, ticket.created_by)

        result = await NotificationUtils.send_ticket_create_notification(
            ticket_id=ticket.id,
            title=ticket.title or "",
            project_name=ticket.project_name or "",
            operator=operator,
            deadline_at=ticket.deadline_at,
            user_names=[ticket.assigned_to],
            token=None,
        )
        logger.info(f"新建工单通知已发送: task_id={body.task_id}, assignee={ticket.assigned_to}, operator={operator}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"发送新建工单通知失败 task_id={body.task_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"发送新建工单通知失败: {str(e)}")


@router.post("/{task_id}/internal/broadcast-comment")
async def internal_broadcast_comment(
    task_id: int,
    comment_id: int = Body(..., embed=True),
    _: str = Depends(verify_sync_api_key),
):
    """AI 服务写库后回调：把指定评论实时广播到 WS 房间（跨进程 pub-sub）。

    AI 服务是独立进程，持有 DB 连接但不持有后端 WS 连接；故它在 task_comments 写库后
    best-effort 回调此端点，由后端按 comment_id 加载评论并广播 comment.created，
    使在线客户端实时上屏 AI 回复（讨论/摘要/诊断）。
    鉴权走 X-API-Key（与用户 JWT 分离），需与后端 HELPDESK_SYNC_API_KEY 一致。
    """
    from app.models.task import TaskComment
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        comment = db.get(TaskComment, comment_id)
    finally:
        db.close()
    if not comment or comment.task_id != task_id:
        raise HTTPException(status_code=404, detail="评论不存在")
    await ws_broadcast_comment("comment.created", task_id, comment)
    return {"code": 0, "message": "broadcasted"}


@router.post("/{task_id}/internal/broadcast-ai-progress")
async def internal_broadcast_ai_progress(
    task_id: int,
    body: dict = Body(...),
    _: str = Depends(verify_sync_api_key),
):
    """AI 服务跨进程回调：把 AI 执行过程（ai.progress）广播进该工单 WS 房间。

    AI 服务在 Supervisor 派发能力期间逐项推送进度（Claude Code 式动态执行过程），
    由后端转广播给在线客户端实时展示；最终 reply 只写纯答复（不含过程块）。
    鉴权走 X-API-Key，与广播评论一致。best-effort，失败不阻塞 AI 主流程。
    """
    run_id = body.get("run_id")
    phase = body.get("phase", "running")
    todos = body.get("todos") or []
    await manager.broadcast(task_id, {
        "type": "ai.progress",
        "run_id": run_id,
        "phase": phase,
        "todos": todos,
    })
    return {"code": 0, "message": "broadcasted"}


@router.get("/attachments/download")
async def download_attachment(
    path: str = Query(..., description="MinIO 对象路径，如 bucket/object_key"),
    filename: Optional[str] = Query(None, description="下载时的文件名"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    """代理下载 MinIO 文件：从 MinIO 读取文件流，通过后端返回给前端下载。

    支持任意格式下载；查找策略：
    1) 严格按存储路径 bucket/object 查找；
    2) 跨已知 bucket 兜底（同一 object 名可能落在不同 bucket）；
    3) 对 object 名做 URL 编码后再试一次（兼容个别上传把中文名编码存储的情况）。
    不再静默吞掉 S3Error，便于定位 404。
    """
    import logging
    from fastapi.responses import StreamingResponse
    from io import BytesIO
    from urllib.parse import unquote, quote
    import os

    logger = logging.getLogger(__name__)

    # 二进制 / 办公 / 压缩等无法在浏览器内联渲染的格式：强制 octet-stream，
    # 避免浏览器把压缩包等当「文档」尝试渲染（控制台 "interpreted as Document" 警告）而走下载。
    BINARY_EXTS = {
        '.zip', '.bz2', '.gz', '.tar', '.tgz', '.rar', '.7z', '.xz',
        '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.exe', '.dmg', '.apk', '.bin', '.iso',
    }

    try:
        decoded_path = unquote(path)

        if decoded_path.startswith('http://') or decoded_path.startswith('https://'):
            from urllib.parse import urlparse
            parsed = urlparse(decoded_path)
            decoded_path = parsed.path.lstrip('/')

        parts = decoded_path.split('/', 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail=f"无效的文件路径: {path}")

        bucket_name, object_name = parts
        download_name = filename or os.path.basename(object_name)
        encoded_name = f"UTF-8''{quote(download_name)}"

        known_buckets = [settings.MINIO_BUCKET, settings.COMMENT_BUCKET, settings.FILE_IMAGES]

        # 候选 (bucket, object) 组合：严格路径 → 跨 bucket 兜底 → 编码 object 名再各试一次
        candidates = [(bucket_name, object_name)]
        for b in known_buckets:
            candidates.append((b, object_name))
        candidates.append((bucket_name, quote(object_name)))
        for b in known_buckets:
            candidates.append((b, quote(object_name)))

        last_err: Optional[Exception] = None
        for bucket, obj in candidates:
            try:
                if not minio_client.check_bucket_exists(bucket):
                    continue

                stat = minio_client.get_file_info(f"{bucket}/{obj}")
                if not stat:
                    continue

                data = minio_client.client.get_object(bucket, obj)
                file_data = data.read()
                data.close()

                ext = os.path.splitext(download_name)[1].lower()
                if ext in BINARY_EXTS:
                    media_type = 'application/octet-stream'
                elif ext == '.pdf':
                    media_type = 'application/pdf'
                elif ext == '.json':
                    media_type = 'application/json'
                else:
                    media_type = stat.content_type or 'application/octet-stream'

                return StreamingResponse(
                    BytesIO(file_data),
                    media_type=media_type,
                    headers={
                        'Content-Disposition': f"attachment; filename*={encoded_name}",
                        'Content-Length': str(len(file_data)),
                        'Access-Control-Expose-Headers': 'Content-Disposition',
                        'Cache-Control': 'no-store',
                    }
                )
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001 - 记录真实原因而非静默跳过
                last_err = e
                logger.warning('[attachments/download] 候选 (%s/%s) 失败: %s', bucket, obj, e)
                continue

        detail = f"文件不存在: {bucket_name}/{object_name}"
        if last_err:
            detail += f"（末次错误: {last_err}）"
        raise HTTPException(status_code=404, detail=detail)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载文件失败: {str(e)}")

