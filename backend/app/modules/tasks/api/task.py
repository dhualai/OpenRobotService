"""tasks 任务管理 API（承接 fqa/ticket）。

MIGRATION.md 阶段 3：从 `app/modules/fqa/ticket/api/ticket.py` 搬迁而来，
路由前缀从 `/api/fqa/tickets` 迁移到 `/api/tasks`。

Wave 2.2 完成：工单(tickets)已升格为任务(tasks)，本模块使用统一的 Task/TaskComment 模型。
"""
import logging

from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File, Form, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from app.core.database import get_async_db as get_db, db_manager
from app.core.auth_routes import get_current_active_user_from_token
from app.modules.admin.api.auth import has_permission_code
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from app.modules.tasks.read_receipt import fetch_comment_read_list, report_read
from app.modules.tasks.schemas.ticket import (
    TicketCreate, TicketUpdate, TicketResponse, TicketListResponse,
    TicketCommentCreate, TicketCommentUpdate, TicketCommentResponse,
    TicketQueryParams, TicketCuibanNotification, TicketFilterRequest,
    TicketCreateNotificationRequest, ProjectMemberResponse
)
from app.modules.tasks.models.ticket import TicketStatus, TicketPriority, TicketType
from app.modules.tasks.services.ticket_service import TicketService, convert_to_shanghai_time
from app.modules.tasks.services.operation_log_service import OperationLogService, get_role_prefix
from app.models.task import OperationType, TaskStep
from app.modules.tasks.api.ws import (
    ws_broadcast_comment,
    ws_broadcast_comment_deleted,
    ws_broadcast_task_updated,
    ws_broadcast_read_receipt,
    manager,
)
from app.utils.minio_client import minio_client
from app.utils.notification_utils import NotificationUtils, _format_shanghai
from app.integrations.api import verify_sync_api_key
from app.core.config import settings
from app.core.user_identity import user_matches, is_admin_user, to_user_id, actor_username, identity_keys
from app.services.redispatch_tip_service import build_redispatch_tip_detail  # 派单说明话术生成（模板+可选AI润色）

router = APIRouter(tags=["tasks"])

# 模块级 logger（避免每个端点内重复 logging.getLogger(__name__)）
logger_task = logging.getLogger(__name__)

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


# 画像缺失英文字段 → 中文展示（供派单情商话术点明缺失项）
_PROFILE_MISSING_LABEL = {
    "department": "部门",
    "job_level": "职级",
    "responsibility_modules": "责任模块",
}


def _fallback_redispatch_candidates() -> List[Dict]:
    """候选快照为空时的兜底：拉全部启用工程师（users.status='active'，与派单权威口径一致），
    按「有画像优先、无画像殿后」排序，供重派弹窗在无精排候选时仍能选择。

    场景：老工单首次派单走了 Step0/精排不足导致落库 candidates 为空 → 重派弹窗「暂无精排候选」死锁。
    此兜底保证弹窗永远有可选项；重派落地后由派单流水线重新生成完整快照覆盖。
    """
    try:
        from app.services.user_service import UserService
        users = UserService.get_user_list(limit=999999999)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning(f"拉取全部用户作重派兜底候选失败: {e}")
        return []

    def _has_profile(u: Dict) -> bool:
        if (u.get("department") or "").strip():
            return True
        rm = u.get("responsibility_modules") or {}
        if isinstance(rm, dict) and any(rm.values()):
            return True
        if isinstance(rm, list) and rm:
            return True
        if u.get("job_level"):
            return True
        return False

    def _eligible(u: Dict) -> bool:
        """是否可为候选：需有可辨识姓名（非“微信用户/无名字”占位），且至少有部门或模块画像，
        避免把微信客服号/空画像噪音用户排进重派弹窗。"""
        name = (u.get("name") or "").strip()
        if not name or name in ("微信用户", "。。", "无"):
            return False
        return _has_profile(u)

    # 仅取启用用户；过滤掉无辨识、无画像的噪音；按“有画像优先”稳定排序（同画像保留原顺序）
    actives = [u for u in users
               if (u.get("status") or "").lower() == "active" and _eligible(u)]
    actives.sort(key=_has_profile, reverse=True)

    out: List[Dict] = []
    for i, u in enumerate(actives, 1):
        uid = u.get("id")
        if not uid:
            continue
        name = u.get("name") or u.get("username") or str(uid)
        if not name:
            continue
        rm = u.get("responsibility_modules") or {}
        if isinstance(rm, dict):
            modules = [k for k, v in rm.items() if v]
        elif isinstance(rm, list):
            modules = list(rm)
        else:
            modules = []
        # 画像缺失项（与前端 hasProfile/hasProfile 权威 missing 字段口径一致）
        missing = []
        if not (u.get("department") or "").strip():
            missing.append("department")
        if not modules:
            missing.append("responsibility_modules")
        out.append({
            "rank": i,
            "engineer_id": str(uid),
            "name": str(name),
            "department": u.get("department"),
            "job_level": u.get("job_level"),
            "modules": modules or [],
            "duty": u.get("duty_text"),
            "missing": missing,
            "scores": {"llm": 0, "semantic": 0, "history": 0, "total": 0},
            "tags": [],
        })
    return out


# 注：派单说明（tip_detail）话术生成已抽离到独立 service，见
# app.services.redispatch_tip_service.build_redispatch_tip_detail


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


# 最近 step 更新的"操作方标识"：与 assigned_to / created_by 字段无关，只表示角色侧，
# 便于判定工单当前该轮到哪一方。
_ACTOR_SIDE_ASSIGNED = "assigned"
_ACTOR_SIDE_CREATOR = "creator"


def _actor_side(ticket, current_user, username: str) -> Optional[str]:
    """返回当前操作人属于接单人侧(assigned) 还是 提单人侧(creator)，都不是则 None。"""
    if user_matches(current_user, getattr(ticket, 'assigned_to', None)):
        return _ACTOR_SIDE_ASSIGNED
    if user_matches(current_user, getattr(ticket, 'created_by', None)):
        return _ACTOR_SIDE_CREATOR
    if username and username and username == getattr(ticket, 'assigned_to', None):
        return _ACTOR_SIDE_ASSIGNED
    if username and username == getattr(ticket, 'created_by', None):
        return _ACTOR_SIDE_CREATOR
    return None


def _apply_step_update_meta(ticket, current_user, username: str) -> Dict[str, Any]:
    """记录当前 step 更新元信息，并按对手回应规则累加回合。

    返回 dict：{bump_round: bool, round_reached_max: bool}，供调用方决定是否发软提醒。
    """
    from sqlalchemy import func
    side = _actor_side(ticket, current_user, username)
    prev_side = getattr(ticket, 'step_last_updated_by', None)

    # 对手回应：前一方有记录且与当前不同，加 1 回合
    bump_round = False
    if prev_side and side and prev_side != side:
        bump_round = True
        ticket.step_negotiation_round = (getattr(ticket, 'step_negotiation_round', 0) or 0) + 1

    if side:
        ticket.step_last_updated_by = side
        ticket.step_last_updated_at = func.now()

    max_rounds = getattr(ticket, 'step_neg_max_rounds', settings.TICKET_STEP_MAX_NEGOTIATION_ROUNDS) or settings.TICKET_STEP_MAX_NEGOTIATION_ROUNDS
    cur_round = getattr(ticket, 'step_negotiation_round', 0) or 0
    esc_count = int(getattr(ticket, 'escalate_count', 0) or 0)
    is_escalated = esc_count > 0
    return {
        "bump_round": bump_round,
        "round": cur_round,
        "max_rounds": max_rounds,
        "escalate_count": esc_count,
        "escalated": is_escalated,
        # 已升级上报后不再受回合上限限制
        "round_reached_max": (not is_escalated) and cur_round >= max_rounds,
        "round_almost_max": (not is_escalated) and cur_round == max_rounds - 1,
    }


@router.post("/", response_model=TicketResponse)
async def create_task(
    ticket_data: TicketCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        username = actor_username(current_user) if current_user else "system"
        creator_id = to_user_id(current_user.get("id") if isinstance(current_user, dict) else None) or to_user_id(username) or username
        token = current_user.get('token') if isinstance(current_user, dict) else getattr(current_user, "token", None)
        logger.info(f"开始创建任务: title={ticket_data.title[:50] if ticket_data.title else '无标题'}, ticket_type={ticket_data.ticket_type}, created_by={creator_id}")
        
        ticket = await TicketService.create_ticket(db, ticket_data, creator_id, comment_attachment_map, token)
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

        # ── 二次派单感知增强（M2）：组装 redispatch 子对象（读 task_dispatch_log 最新一条）──
        try:
            from app.models.task_dispatch_log import TaskDispatchLog
            from sqlalchemy import select as _sel
            # 权限控制：派单原因（tip_detail）属敏感信息，仅对「提单人」或「管理员」可见，其他人不返回。
            try:
                from app.core.database import get_user_with_roles
                from app.core.user_identity import user_matches, is_admin_user
                from app.core.security import decode_token
                _viewer_creator = False
                _viewer_admin = False
                if token:
                    _payload = decode_token(token)
                    _uname = (_payload or {}).get("sub")
                    if _uname:
                        _viewer = get_user_with_roles(_uname)
                        if _viewer:
                            _viewer_creator = user_matches(_viewer, getattr(ticket, "created_by", None))
                            _viewer_admin = is_admin_user(_viewer)
            except Exception:
                _viewer_creator = False
                _viewer_admin = False
            _log = (await db.execute(
                _sel(TaskDispatchLog)
                .where(TaskDispatchLog.task_id == task_id)
                .order_by(TaskDispatchLog.dispatch_round.desc())
                .limit(1)
            )).scalars().first()
            if _log is not None:
                user_map = await TicketService._get_user_map(token)
                prof = dict(_log.profile or {})
                assigned_name = user_map.get(_log.assigned_id, _log.assigned_id)
                pref_name = user_map.get(_log.preferred_id) if _log.preferred_id else None
                # 二次派单感知增强（M3 高情商回复）：未派到指定人时生成一段「模板为主+AI润色」的完整话术
                # （供详情页展示）。从候选快照取倾向人画像缺失项（missing）判定引导分支；其余分支无此字段。
                tip_detail = None
                if _log.preferred_id and _log.preferred_id != _log.assigned_id and _log.assigned_id:
                    # 倾向人画像缺失项（英文 → 中文）
                    pref_missing_zh = []
                    for cand in (_log.candidates or []):
                        if isinstance(cand, dict) and cand.get("engineer_id") == _log.preferred_id:
                            for f in (cand.get("missing") or []):
                                zh = _PROFILE_MISSING_LABEL.get(str(f), str(f))
                                if zh not in pref_missing_zh:
                                    pref_missing_zh.append(zh)
                            break
                    reasoning_txt = _log.reasoning if isinstance(_log.reasoning, str) else ""
                    # 面向用户展示：reasoning 里若残留候选人 users.id，替换为姓名（避免向提单人暴露内部 id）
                    if reasoning_txt:
                        for _cand in (_log.candidates or []):
                            if isinstance(_cand, dict):
                                _cid = _cand.get("engineer_id")
                                _cname = _cand.get("name") or user_map.get(_cid, _cid)
                                if _cid and _cname:
                                    reasoning_txt = reasoning_txt.replace(f"ID:{_cid}", _cname)
                                    reasoning_txt = reasoning_txt.replace(f"({_cid})", f"({_cname})")
                                    reasoning_txt = reasoning_txt.replace(f"（{_cid}）", f"（{_cname}）")
                                    reasoning_txt = reasoning_txt.replace(_cid, _cname)
                        # 原处理人等不在候选内的 id，用 user_map 兜底反查姓名
                        for _cid, _cname in (user_map or {}).items():
                            if _cname and _cid and isinstance(_cid, str) and _cid in reasoning_txt:
                                reasoning_txt = reasoning_txt.replace(_cid, _cname)
                    tip_detail = await build_redispatch_tip_detail(
                        pref_name or _log.preferred_id,
                        assigned_name,
                        reasoning=reasoning_txt,
                        pref_missing_zh=pref_missing_zh,
                    )
                # 二次派单感知增强（M2 兜底）：候选快照为空（老工单 Step0/精排不足 → 空落库）时，
                # 拉全部启用工程师作兜底候选，保证重派弹窗有可选项；重派落地后由流水线覆盖。
                _cands = _log.candidates if _log.candidates else _fallback_redispatch_candidates()
                setattr(ticket, "redispatch", {
                    "dispatch_round": _log.dispatch_round,
                    "candidates": _cands,
                    "result": {
                        "assigned_id": _log.assigned_id,
                        "assigned_name": assigned_name,
                        "preferred_id": _log.preferred_id,
                        "preferred_name": pref_name,
                        "confidence": _log.confidence,
                        "decision_type": _log.decision_type,
                        "reasoning": _log.reasoning,
                        "profile": {
                            "dept": prof.get("dept"),
                            "job_level": prof.get("job_level"),
                            "modules": prof.get("modules"),
                            "duty": prof.get("duty"),
                            "missing": prof.get("missing") or [],
                        } if prof else None,
                        "matched_pref": _log.matched_pref,
                        "name_collision": _log.name_collision,
                        "pinyin_match": _log.pinyin_match,
                        # 派单原因仅对提单人/管理员可见；其他查看者不返回（前端不渲染派单说明）
                        "tip_detail": tip_detail if (_viewer_creator or _viewer_admin) else None,
                    },
                })
            else:
                # 无派单日志（老工单/未派过单）：重派弹窗没有候选会形成「无法选人→无法重派→无新日志」死锁，
                # 故仍给兜底候选（拉全部启用工程师，有画像优先），保证弹窗有可选项。重派落地后由流水线覆盖。
                setattr(ticket, "redispatch", {
                    "dispatch_round": 0,
                    "candidates": _fallback_redispatch_candidates(),
                    "result": None,
                })
        except Exception as redisp_err:
            logger.warning(f"组装 redispatch 失败 task_id={task_id}: {redisp_err}")

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
    all: bool = Query(False, description="为 true 时在项目成员基础上追加返回全部在职用户（用于 @ 时按关键字过滤到项目外的人）"),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    """获取任务关联项目的成员列表 + 工单处理人（用于讨论区 @ 提及）。

    all=false：仅返回提单人/处理人 + 项目成员（默认候选池）。
    all=true：在前者基础上再追加全部 active 在职用户（已去重），
             使讨论区输入 @关键字 时可过滤到项目外的人。
    """
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

        # ── 3. all=true：追加全部 active 在职用户（去重），漏出项目外的人供 @ 过滤 ──
        if all:
            from app.core.db import SessionLocal
            from app.models.identity import UserDB
            sync_db = SessionLocal()
            try:
                all_users = sync_db.query(UserDB).filter(UserDB.status == "active").all()
                # 按姓名、用户名排序，保证姓名相近的排在一起
                def _sort_key(u):
                    return (u.name or u.username or "").lower()
                all_users.sort(key=_sort_key)
                for u in all_users:
                    uname = (u.username or "").strip()
                    if not uname or uname in seen:
                        continue
                    seen.add(uname)
                    result.append(ProjectMemberResponse(
                        id=uname,
                        username=uname,
                        name=u.name or uname,
                        role_name=None,
                    ))
            finally:
                sync_db.close()

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

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    user_name = (current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", username))

    # 拥有 backend:tasks:operate 权限的用户视同 admin，跳过身份与状态流转校验
    can_operate = has_permission_code(current_user, "backend:tasks:operate")
    if not is_admin and not can_operate:
        if not user_matches(current_user, ticket.assigned_to, ticket.customer, ticket.created_by):
            raise HTTPException(status_code=403, detail="无权限更新此任务")
        if ticket.status == TicketStatus.CLOSED:
            raise HTTPException(status_code=400, detail="已关闭的任务不能更新")
        if ticket_update.status:
            if ticket.status == TicketStatus.NEW and not user_matches(current_user, ticket.created_by):
                raise HTTPException(status_code=400, detail="只允许创建者开始任务！")
            if ticket.status in [TicketStatus.PENDING, TicketStatus.IN_PROGRESS] and not user_matches(current_user, ticket.assigned_to):
                raise HTTPException(status_code=400, detail="只允许处理人更新任务！")
            if ticket.status == TicketStatus.RESOLVED and not user_matches(current_user, ticket.customer):
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
            # 升级上报：escalate_count +1，协商回合重置为1，不再受回合上限限制
            prev_count = int(getattr(ticket, 'escalate_count', 0) or 0)
            ticket.escalate_count = prev_count + 1
            ticket.step_negotiation_round = 0
            ticket.curr_step_agreed = False
            await OperationLogService.log(
                db=db, task_id=task_id, op_type=OperationType.ESCALATE,
                operator=username, operator_name=user_name,
                detail={"escalate_count": prev_count + 1, "round_reset": 1},
                description=f"{_role}{user_name} 升级了工单（第{prev_count + 1}次），协商回合重置为1，不再受限" if _role else f"{user_name} 升级了工单（第{prev_count + 1}次），协商回合重置为1，不再受限",
            )
            await _add_system_comment(db, task_id, f"{user_name} 升级了工单（第{prev_count + 1}次），协商回合重置为1，不再受回合上限限制", username, token)
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
            _op_keys = set(identity_keys(username)) | {None}
            reassign_notify_users = [u for u in {ticket.created_by, new_assignee} if u not in _op_keys]
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

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)

    if not is_admin:
        if not user_matches(current_user, ticket.assigned_to, ticket.created_by):
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


class CommentReadReport(BaseModel):
    """已读上报表单（WS 不可用时的 REST 兜底通道）。

    ``comment_ids`` 为本轮实际读到的评论 id 列表，``last_read_comment_id`` 为游标。
    长度上限与服务端清洗上限一致（MAX_COMMENT_IDS_PER_REQUEST）。
    """
    comment_ids: List[int] = Field(default_factory=list, max_length=500)
    last_read_comment_id: Optional[int] = None


class CommentReadRecordItem(BaseModel):
    username: str
    name: Optional[str] = None
    avatar_resource_id: Optional[int] = None
    read_at: Optional[str] = None


@router.post("/{task_id}/comments/read")
async def report_comments_read(
    task_id: int,
    payload: CommentReadReport,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """已读上报的 REST 兜底通道。

    前端在 WS 未连接（尚未建连 / 断线重连中 / 降级）时改走本接口，避免
    「已读帧被静默丢弃后再也不重试」导致的名单漏报。写库成功后同样广播
    read_receipt，房间内在线成员实时可见。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    username = actor_username(current_user)
    user_name = current_user.get('name') or username
    avatar_resource_id = current_user.get('avatar_resource_id')

    # 同步 ORM 走线程池，避免阻塞事件循环（report_read 内部自带独立会话）
    result = await run_in_threadpool(
        report_read,
        task_id,
        username,
        payload.comment_ids,
        user_name,
        avatar_resource_id,
        payload.last_read_comment_id,
    )

    try:
        await ws_broadcast_read_receipt(
            task_id,
            username,
            result["records"],
            result["comment_ids"],
            result["last_read_comment_id"],
        )
    except Exception as e:  # noqa: BLE001
        logger_task.warning(f"已读回执广播失败（已读已落库）task_id={task_id}: {e}")

    return {
        "ok": True,
        "comment_ids": result["comment_ids"],
        "last_read_comment_id": result["last_read_comment_id"],
        "records": result["records"],
    }


@router.get("/{task_id}/comments/{comment_id}/read", response_model=List[CommentReadRecordItem])
async def get_comment_read_list(
    task_id: int,
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """按需拉取单条评论的已读名单（已读弹层打开时刷新，兜底 welcome 快照截断）。"""
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    return await run_in_threadpool(fetch_comment_read_list, task_id, comment_id)


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

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    token = request.headers.get("Authorization", "").replace("Bearer ", "") if request else ""

    # AI 工单（source='ai'）允许任何登录用户操作状态（created_by='system' 不是真实用户）
    if ticket.source == 'ai':
        pass
    elif not user_matches(current_user, ticket.created_by, ticket.assigned_to) and not is_admin:
        raise HTTPException(status_code=403, detail="无权限更新任务状态")

    try:
        status_enum = TicketStatus(status)

        # ── 撤回（→ canceled）权限收窄：仅提单人(created_by) 或 管理员可撤回，处理人(assigned_to) 不可撤回 ──
        # 业务规则：撤回是提单人防止「派错单/误提」的特权，处理人应走「退回/暂停」而非替提单人撤回。
        # created_by 过渡期可能是 username 或 users.id，与当前用户双键比较。
        if status_enum == TicketStatus.CANCELED and not is_admin and not user_matches(current_user, ticket.created_by):
            raise HTTPException(status_code=403, detail="仅提单人或管理员可撤回工单")

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
        user_name = current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", None) or username
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


# ==================== 工单阶段性处理（协商节点） ====================


class RespondRequest(BaseModel):
    """首次响应请求：确认协商节点并开始处理。

    curr_step_id 不传时确认工单当前 curr_step_id（AI 提单时已设置）。
    """
    curr_step_id: Optional[int] = Field(None, description="确认的协商节点ID（不传则确认当前 curr_step_id）")


@router.get("/{task_id}/steps", summary="按工单类型读取协商阶段模板")
async def get_task_steps(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """读取 task_steps 模板（按工单 task_type 过滤，sequence 升序）。

    前端「工单阶段性处理」区域据此生成当前节点描述（如 1/3 进度）。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")
    result = await db.execute(
        select(TaskStep)
        .where(TaskStep.task_type == ticket.task_type)
        .order_by(TaskStep.sequence.asc())
    )
    rows = result.unique().scalars().all()
    steps = [{"id": r.id, "step_name": r.step_name, "sequence": r.sequence} for r in rows]
    return {"code": 0, "data": {"steps": steps}}


@router.post("/{task_id}/respond", response_model=TicketResponse, summary="确认同意：当前协商节点协商一致")
async def respond_task(
    task_id: int,
    body: RespondRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    request: Request = None,
):
    """确认同意：将当前协商节点标记为「已协商一致」。

    两种情形：
    1. 处理人首次响应（status=NEW）：状态 new → in_progress（工单开始处理），同时标记 curr_step_agreed=True。
    2. 处理中（status=IN_PROGRESS）的「对方」确认：仅将 curr_step_agreed 由 False 置 True，
       状态不变（用于新节点推进后再次达成一致）。

    权限：AI 工单允许任何登录用户；其余需处理人/提单人/管理员/操作权限。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    token = request.headers.get("Authorization", "").replace("Bearer ", "") if request else ""
    can_operate = has_permission_code(current_user, "backend:tasks:operate")

    # AI 工单（created_by='system'）允许任何登录用户操作；其余需处理人/提单人/管理员/操作权限
    if ticket.source == 'ai':
        pass
    elif not (user_matches(current_user, ticket.assigned_to) or user_matches(current_user, ticket.created_by) or is_admin or can_operate):
        raise HTTPException(status_code=403, detail="无权限响应此工单")

    old_status = ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status)
    is_first_response = (ticket.status == TicketStatus.NEW)
    if not is_first_response and ticket.status != TicketStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="当前状态不可确认同意")
    if getattr(ticket, 'curr_step_agreed', False) and is_first_response is False:
        # 协商一致阶段重复点确认同意：避免覆盖（无操作意义）
        raise HTTPException(status_code=400, detail="当前节点已协商一致，无需重复确认")

    # 确认协商节点：优先取 body.curr_step_id，否则用工单现有 curr_step_id
    step_id = body.curr_step_id if body.curr_step_id is not None else ticket.curr_step_id
    if step_id is None:
        raise HTTPException(status_code=400, detail="请先设置协商节点后再响应")

    # 反查节点名称，保证 curr_step_name 与模板一致
    step_row = await db.execute(select(TaskStep).where(TaskStep.id == int(step_id)))
    step = step_row.unique().scalar_one_or_none()
    step_name = step.step_name if step else (ticket.curr_step_name or "")

    ticket.curr_step_id = int(step_id)
    ticket.curr_step_name = step_name
    if is_first_response:
        ticket.status = TicketStatus.IN_PROGRESS
    ticket.curr_step_agreed = True
    # 确认同意时同步更新工单截止时间为当前节点时间
    if ticket.curr_step_endtime:
        ticket.deadline_at = ticket.curr_step_endtime
    ticket.updated_at = func.now()

    # 确认同意是"达成一致"的正向动作，不计入协商回合（回合只在"协商节点时间"时累加），
    # 但需记录操作方元信息，供下一节点的回合归属判定使用。
    side = _actor_side(ticket, current_user, username)
    if side:
        ticket.step_last_updated_by = side
        ticket.step_last_updated_at = func.now()
    await db.commit()

    # 操作日志 + 系统评论
    user_name = current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", None) or username
    _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username)
    if is_first_response:
        await OperationLogService.log(
            db=db,
            task_id=task_id,
            op_type=OperationType.STATUS_CHANGE,
            operator=username,
            operator_name=user_name,
            to_status=TicketStatus.IN_PROGRESS.value,
            detail={"from": old_status, "to": TicketStatus.IN_PROGRESS.value},
            description=f"{_role}{user_name} 确认协商节点「{step_name}」，开始处理" if _role else f"{user_name} 确认协商节点「{step_name}」，开始处理",
        )
        await _add_system_comment(
            db, task_id,
            f"{user_name} 确认协商节点「{step_name}」，开始处理工单",
            username, token,
        )
    else:
        await OperationLogService.log(
            db=db,
            task_id=task_id,
            op_type=OperationType.UPDATE,
            operator=username,
            operator_name=user_name,
            detail={"curr_step_id": int(step_id), "curr_step_agreed": True},
            description=f"{_role}{user_name} 确认同意节点「{step_name}」，达成协商一致" if _role else f"{user_name} 确认同意节点「{step_name}」，达成协商一致",
        )
        await _add_system_comment(
            db, task_id,
            f"{user_name} 确认同意节点「{step_name}」，达成协商一致",
            username, token,
        )
    try:
        await ws_broadcast_task_updated(task_id, ticket)
    except Exception:
        pass
    return await _reload_ticket_with_comments(db, task_id)


@router.post("/{task_id}/complete-step", response_model=TicketResponse, summary="当前阶段完成：推进到下一协商节点")
async def complete_task_step(
    task_id: int,
    body: CompleteStepRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    request: Request = None,
):
    """当前协商节点完成：处理人选择同类型的下一阶段节点并设置其结束时间。

    - next_step_id 必须为同 task_type 的节点；推进后 curr_step_agreed 重置为 False，
      工单进入"未一致"状态，等待对方（创建人）「确认同意」后才视为协商一致。
    - curr_step_endtime 为新节点的结束时间（SLA）。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    token = request.headers.get("Authorization", "").replace("Bearer ", "") if request else ""
    can_operate = has_permission_code(current_user, "backend:tasks:operate")

    if ticket.source == 'ai':
        pass
    elif not (user_matches(current_user, ticket.assigned_to) or is_admin or can_operate):
        raise HTTPException(status_code=403, detail="无权限操作此工单")

    if ticket.status != TicketStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="仅处理中的工单可完成当前阶段")
    if not getattr(ticket, 'curr_step_agreed', False):
        raise HTTPException(status_code=400, detail="当前节点尚未协商一致，无法推进到下一阶段")
    if ticket.curr_step_id is None:
        raise HTTPException(status_code=400, detail="当前节点不存在，无法推进")

    # 反查当前节点，用于日志与回退校验
    cur_row = await db.execute(select(TaskStep).where(TaskStep.id == int(ticket.curr_step_id)))
    cur_step = cur_row.unique().scalar_one_or_none()
    if cur_step is None:
        raise HTTPException(status_code=400, detail="当前节点不存在，无法推进")

    # 校验目标节点：必须为同 task_type，且 sequence > 当前节点（只能向前推进）
    next_row = await db.execute(
        select(TaskStep).where(TaskStep.id == int(body.next_step_id))
    )
    next_step = next_row.unique().scalar_one_or_none()
    if next_step is None:
        raise HTTPException(status_code=400, detail="所选下一阶段节点不存在")
    if next_step.task_type != ticket.task_type:
        raise HTTPException(status_code=400, detail="下一阶段节点与工单类型不匹配")
    if next_step.sequence <= cur_step.sequence:
        raise HTTPException(status_code=400, detail="下一阶段必须晚于当前阶段")

    old_step_name = ticket.curr_step_name or cur_step.step_name
    endtime = convert_to_shanghai_time(body.curr_step_endtime)
    ticket.curr_step_id = next_step.id
    ticket.curr_step_name = next_step.step_name
    ticket.curr_step_endtime = endtime
    ticket.deadline_at = endtime  # 新阶段首次设置时间 → 更新工单截止时间
    ticket.curr_step_agreed = False  # 进入新节点：等待对方确认同意才视为协商一致
    ticket.step_phase_round = int(getattr(ticket, 'step_phase_round', 0) or 0) + 1
    ticket.updated_at = func.now()

    # 阶段完成 = 当前操作人"提案"推进到下一节点：记录操作方（回合归属）
    round_meta = _apply_step_update_meta(ticket, current_user, username)
    # 进入新节点 = 新一轮协商的开始：协商回合重置为第 1 回合（处理人推进提案为该节点首轮），
    # 避免上一节点累计的回合把新节点直接带到"满回合/升级上报"状态
    ticket.step_negotiation_round = 1
    round_meta = {**round_meta, "bump_round": False, "round": 1}
    await db.commit()

    # 操作日志 + 系统评论
    user_name = current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", None) or username
    _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username)
    endtime_label = _format_shanghai(endtime)
    await OperationLogService.log(
        db=db,
        task_id=task_id,
        op_type=OperationType.UPDATE,
        operator=username,
        operator_name=user_name,
        detail={
            "from_step": old_step_name,
            "to_step": next_step.step_name,
            "curr_step_endtime": endtime.isoformat() if endtime else None,
            "negotiation_round": round_meta["round"],
        },
        description=f"{_role}{user_name} 完成阶段「{old_step_name}」，进入「{next_step.step_name}」（节点时间 {endtime_label}）" if _role else f"{user_name} 完成阶段「{old_step_name}」，进入「{next_step.step_name}」（节点时间 {endtime_label}）",
    )
    comment_lines = [f"{user_name} 完成阶段「{old_step_name}」，进入「{next_step.step_name}」（节点时间 {endtime_label}）"]
    if round_meta["bump_round"]:
        comment_lines.append(f"（本轮协商回合：{round_meta['round']}/{round_meta['max_rounds']}）")
        if round_meta["round_almost_max"]:
            comment_lines.append("⚠️ 已临近最大协商回合，请尽快收敛；若仍无法达成一致可使用升级上报。")
    await _add_system_comment(
        db, task_id,
        "".join(comment_lines),
        username, token,
    )
    try:
        await ws_broadcast_task_updated(task_id, ticket)
    except Exception:
        pass
    return await _reload_ticket_with_comments(db, task_id)


class NegotiateStepRequest(BaseModel):
    """协商节点请求：可调整节点为当前或之后（sequence >= 当前）+ 设置节点结束时间，理由必填。"""
    curr_step_endtime: datetime = Field(..., description="协商节点结束时间（ISO 字符串，naive UTC 存库）")
    curr_step_id: Optional[int] = Field(None, description="协商后的节点ID（仅当前及之后；不传则保持当前节点）")
    reason: str = Field(..., description="协商理由（必填，记录为评论）")


class CompleteStepRequest(BaseModel):
    """当前阶段完成请求：处理人选择下一阶段节点并设置其结束时间。"""
    next_step_id: int = Field(..., description="下一阶段节点ID（必须为同 task_type 的节点）")
    curr_step_endtime: datetime = Field(..., description="下一阶段节点结束时间（ISO 字符串，naive UTC 存库）")


@router.post("/{task_id}/negotiate-step", response_model=TicketResponse, summary="协商节点：调整节点并设置节点结束时间")
async def negotiate_step(
    task_id: int,
    body: NegotiateStepRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    request: Request = None,
):
    """协商节点：可将当前节点调整为当前或之后的任一节点，并设置节点结束时间（SLA）。

    协商理由必填，作为系统评论记录。
    权限：AI 工单允许任何登录用户；其余需处理人/管理员/操作权限。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    token = request.headers.get("Authorization", "").replace("Bearer ", "") if request else ""
    can_operate = has_permission_code(current_user, "backend:tasks:operate")

    # 权限：接单人 / 提单人 / 管理员 / 操作权限均可协商（回合双方对话）
    _is_assignee = user_matches(current_user, ticket.assigned_to)
    _is_creator = user_matches(current_user, ticket.created_by)
    if ticket.source != 'ai' and not (_is_assignee or _is_creator or is_admin or can_operate):
        raise HTTPException(status_code=403, detail="无权限协商此工单")

    # 协商理由必填
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="协商理由必填")

    # 节点调整：校验目标节点存在且与工单类型匹配
    # 第一轮（step_phase_round==0，未被"当前阶段完成"推进过）不限制 sequence，可任选节点；
    # 之后仅允许当前及之后（sequence >= 当前）
    phase_round = int(getattr(ticket, 'step_phase_round', 0) or 0)
    old_step_name = ticket.curr_step_name
    step_changed = False
    if body.curr_step_id is not None and int(body.curr_step_id) != ticket.curr_step_id:
        # 反查当前节点 sequence 用于下限校验
        cur_row = await db.execute(select(TaskStep).where(TaskStep.id == int(ticket.curr_step_id)))
        cur_step = cur_row.unique().scalar_one_or_none()
        cur_seq = cur_step.sequence if cur_step else 0

        row = await db.execute(select(TaskStep).where(TaskStep.id == int(body.curr_step_id)))
        target_step = row.unique().scalar_one_or_none()
        if target_step is None:
            raise HTTPException(status_code=400, detail="协商节点不存在")
        if target_step.task_type != ticket.task_type:
            raise HTTPException(status_code=400, detail="协商节点与工单类型不匹配")
        if phase_round > 0 and target_step.sequence < cur_seq:
            raise HTTPException(status_code=400, detail="协商节点不能早于当前节点")
        ticket.curr_step_id = target_step.id
        ticket.curr_step_name = target_step.step_name
        step_changed = True

    # 前端 dayjs(...).toISOString() 传入 UTC aware datetime，剥时区转 naive UTC 存库
    # 协商节点时间只更新 curr_step_endtime，不动 deadline_at；
    # 待"确认同意"(/respond) 时再把 deadline_at 同步到已协商一致的节点时间。
    endtime = convert_to_shanghai_time(body.curr_step_endtime)
    ticket.curr_step_endtime = endtime
    ticket.updated_at = func.now()

    # 处理人首次响应（协商节点时间）：工单状态 new → in_progress
    old_status = ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status)
    status_transitioned = False
    if ticket.status == TicketStatus.NEW:
        ticket.status = TicketStatus.IN_PROGRESS
        status_transitioned = True
    # 任何协商（含节点变更/时间调整）都视为新一轮提案，重置协商一致状态
    ticket.curr_step_agreed = False

    # 回合计数：对手回应 +1
    round_meta = _apply_step_update_meta(ticket, current_user, username)
    await db.commit()

    user_name = current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", None) or username
    _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username)
    # 节点时间按东八区展示（DB 为 naive UTC）
    endtime_label = _format_shanghai(endtime)
    action_desc = (
        f"协商将节点「{old_step_name}」调整为「{ticket.curr_step_name}」（节点时间 {endtime_label}）"
        if step_changed
        else f"协商节点「{ticket.curr_step_name}」时间（{endtime_label}）"
    )
    await OperationLogService.log(
        db=db,
        task_id=task_id,
        op_type=OperationType.UPDATE,
        operator=username,
        operator_name=user_name,
        detail={
            "from_step": old_step_name if step_changed else None,
            "to_step": ticket.curr_step_name,
            "curr_step_endtime": endtime.isoformat() if endtime else None,
            "negotiation_round": round_meta["round"],
        },
        description=f"{_role}{user_name} {action_desc}" if _role else f"{user_name} {action_desc}",
    )
    # 首次响应触发的状态变更单独记录一条 STATUS_CHANGE 日志，与 respond 接口保持一致
    if status_transitioned:
        await OperationLogService.log(
            db=db,
            task_id=task_id,
            op_type=OperationType.STATUS_CHANGE,
            operator=username,
            operator_name=user_name,
            to_status=TicketStatus.IN_PROGRESS.value,
            detail={"from": old_status, "to": TicketStatus.IN_PROGRESS.value},
            description=f"{_role}{user_name} 首次响应协商节点，工单进入处理中" if _role else f"{user_name} 首次响应协商节点，工单进入处理中",
        )
    comment_lines = [f"{user_name} {action_desc}，理由：{reason}"]
    if status_transitioned:
        comment_lines.append("（首次响应，工单状态变更为「处理中」）")
    if round_meta["bump_round"]:
        comment_lines.append(f"（本轮协商回合：{round_meta['round']}/{round_meta['max_rounds']}）")
        if round_meta["round_almost_max"]:
            comment_lines.append("⚠️ 已临近最大协商回合，请尽快收敛；若仍无法达成一致可使用升级上报。")
    await _add_system_comment(
        db, task_id,
        "".join(comment_lines),
        username, token,
    )
    try:
        await ws_broadcast_task_updated(task_id, ticket)
    except Exception:
        pass
    return await _reload_ticket_with_comments(db, task_id)


class SetStepTimeRequest(BaseModel):
    """设置节点时间请求：处理人一锤定音，直接设置当前节点结束时间，跳过协商。"""
    curr_step_endtime: datetime = Field(..., description="节点结束时间（ISO 字符串，naive UTC 存库）")


@router.post("/{task_id}/set-step-time")
async def set_step_time(
    task_id: int,
    body: SetStepTimeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    request: Request = None,
):
    """设置节点时间：处理人一锤定音，直接设置节点结束时间并标记为协商一致。

    仅限已升级上报（escalate_count > 0）的工单，仅处理人/管理员可调用。
    调用后 curr_step_agreed=True，不再进入协商回合。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    token = request.headers.get("Authorization", "").replace("Bearer ", "") if request else ""
    can_operate = has_permission_code(current_user, "backend:tasks:operate")

    # 仅处理人/管理员/操作权限
    _is_assignee = user_matches(current_user, ticket.assigned_to)
    if ticket.source != 'ai' and not (_is_assignee or is_admin or can_operate):
        raise HTTPException(status_code=403, detail="无权限设置节点时间")

    # 仅已升级上报的工单可用
    esc_count = int(getattr(ticket, 'escalate_count', 0) or 0)
    if esc_count <= 0:
        raise HTTPException(status_code=400, detail="仅已升级上报的工单可使用此功能")

    # 设置节点结束时间 + 一锤定音（协商一致）
    endtime = body.curr_step_endtime
    ticket.curr_step_endtime = endtime
    ticket.deadline_at = endtime  # 一锤定音设置时间 → 更新工单截止时间
    ticket.curr_step_agreed = True
    ticket.step_last_updated_by = 'assigned'
    ticket.step_last_updated_at = func.now()
    await db.commit()
    await db.refresh(ticket)

    user_name = await _resolve_display_name(current_user, ticket, db, token)
    _role = "处理人" if _is_assignee else ("管理员" if is_admin else "")

    await OperationLogService.log(
        db=db, task_id=task_id, op_type=OperationType.UPDATE,
        operator=username, operator_name=user_name,
        detail={
            "curr_step_endtime": endtime.isoformat() if endtime else None,
            "finalized": True,
            "escalate_count": esc_count,
        },
        description=f"{_role}{user_name} 一锤定音设置节点时间" if _role else f"{user_name} 一锤定音设置节点时间",
    )
    await _add_system_comment(
        db, task_id,
        f"{user_name} 设置节点时间为 {endtime.strftime('%Y-%m-%d %H:%M')}（升级上报后一锤定音，不再协商）",
        username, token,
    )
    try:
        await ws_broadcast_task_updated(task_id, ticket)
    except Exception:
        pass
    return await _reload_ticket_with_comments(db, task_id)


class ReopenStepRequest(BaseModel):
    """未解决打回请求：提单人选择重新开始的阶段节点 + 节点结束时间。"""
    curr_step_id: int = Field(..., description="重新开始的阶段节点ID（必须为同 task_type 的节点）")
    curr_step_endtime: datetime = Field(..., description="节点结束时间（ISO 字符串，naive UTC 存库）")


@router.post("/{task_id}/reopen-step", response_model=TicketResponse, summary="未解决打回：回到处理中并从头开始阶段性处理")
async def reopen_step(
    task_id: int,
    body: ReopenStepRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    request: Request = None,
):
    """未解决打回：已解决工单由提单人打回到处理中，阶段性处理从头再开始。

    - 仅已解决（resolved）状态的工单可打回；权限：提单人/管理员/操作权限（AI 工单放行）。
    - 目标节点必须为同 task_type 的节点（通常选择第一阶段）。
    - 打回后：curr_step_agreed=False（等待处理人确认同意）、协商回合重置为 1、回合归属设为提单人（轮到处理人响应）。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    token = request.headers.get("Authorization", "").replace("Bearer ", "") if request else ""
    can_operate = has_permission_code(current_user, "backend:tasks:operate")

    # 权限：提单人 / 管理员 / 操作权限（AI 工单放行）
    _is_creator = user_matches(current_user, ticket.created_by)
    if ticket.source != 'ai' and not (_is_creator or is_admin or can_operate):
        raise HTTPException(status_code=403, detail="仅提单人可打回工单")

    # 仅已解决状态可打回
    if ticket.status != TicketStatus.RESOLVED:
        raise HTTPException(status_code=400, detail="仅已解决的工单可打回")

    # 校验目标节点：必须为同 task_type
    row = await db.execute(select(TaskStep).where(TaskStep.id == int(body.curr_step_id)))
    target_step = row.unique().scalar_one_or_none()
    if target_step is None:
        raise HTTPException(status_code=400, detail="所选阶段节点不存在")
    if target_step.task_type != ticket.task_type:
        raise HTTPException(status_code=400, detail="所选阶段节点与工单类型不匹配")

    old_status = ticket.status.value if hasattr(ticket.status, 'value') else str(ticket.status)
    old_step_name = ticket.curr_step_name

    # 状态回到处理中 + 阶段性处理从头开始
    endtime = convert_to_shanghai_time(body.curr_step_endtime)
    ticket.status = TicketStatus.IN_PROGRESS
    ticket.curr_step_id = target_step.id
    ticket.curr_step_name = target_step.step_name
    ticket.curr_step_endtime = endtime
    ticket.deadline_at = endtime  # 打回重设节点时间 → 更新工单截止时间
    ticket.curr_step_agreed = False
    ticket.step_negotiation_round = 0
    ticket.step_phase_round = 0  # 打回重开：阶段回合数归零，回到第一轮
    ticket.step_last_updated_by = 'creator'  # 提单人打回提案 → 轮到处理人确认
    ticket.step_last_updated_at = func.now()
    ticket.resolved_at = None
    ticket.updated_at = func.now()
    await db.commit()

    user_name = current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", None) or username
    _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username)
    endtime_label = _format_shanghai(endtime)

    # 状态变更日志 + 系统评论
    await OperationLogService.log(
        db=db,
        task_id=task_id,
        op_type=OperationType.STATUS_CHANGE,
        operator=username,
        operator_name=user_name,
        to_status=TicketStatus.IN_PROGRESS.value,
        detail={"from": old_status, "to": TicketStatus.IN_PROGRESS.value},
        description=f"{_role}{user_name} 标记工单未解决，打回到处理中" if _role else f"{user_name} 标记工单未解决，打回到处理中",
    )
    # 阶段重置日志
    await OperationLogService.log(
        db=db,
        task_id=task_id,
        op_type=OperationType.UPDATE,
        operator=username,
        operator_name=user_name,
        detail={
            "from_step": old_step_name,
            "to_step": target_step.step_name,
            "curr_step_endtime": endtime.isoformat() if endtime else None,
            "round_reset": 1,
        },
        description=f"{_role}{user_name} 打回重开阶段性处理：从「{target_step.step_name}」重新开始（节点时间 {endtime_label}）" if _role else f"{user_name} 打回重开阶段性处理：从「{target_step.step_name}」重新开始（节点时间 {endtime_label}）",
    )
    await _add_system_comment(
        db, task_id,
        f"{user_name} 标记工单未解决，打回到处理中：阶段性处理从「{target_step.step_name}」重新开始（节点时间 {endtime_label}），等待处理人确认",
        username, token,
    )
    try:
        await ws_broadcast_task_updated(task_id, ticket)
    except Exception:
        pass
    return await _reload_ticket_with_comments(db, task_id)


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
        _op_keys = set(identity_keys(username)) | {None}
        assign_notify_users = [u for u in {ticket.created_by, user_id} if u not in _op_keys]
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


class CreatorNameUpdate(BaseModel):
    """更新工单创建人姓名请求体。仅更新 users.name，不改变工单 created_by。"""
    name: str


@router.patch("/{task_id}/creator-name", summary="更新工单创建人姓名（仅处理人/管理员可操作）")
async def update_creator_name(
    task_id: int,
    payload: CreatorNameUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """通过工单 created_by 反查用户表并更新其 name；不改变 created_by 本身。

    权限：仅工单处理人（assigned_to）或管理员可操作。创建人姓名为后端从 users 表
    实时解析的派生字段（无独立列），故更新用户 name 后，工单 created_by_name 自动刷新。
    """
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    user_name = (current_user.get('name', username) if isinstance(current_user, dict) else username)

    # 仅工单处理人（assigned_to）或管理员可修改创建人姓名
    if not is_admin and not user_matches(current_user, ticket.assigned_to):
        raise HTTPException(status_code=403, detail="仅工单处理人可修改创建人姓名")

    new_name = (payload.name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="创建人姓名不能为空")

    created_by = getattr(ticket, "created_by", None)
    if not created_by:
        raise HTTPException(status_code=400, detail="工单无创建人，无法更新姓名")

    # created_by 存 users.id；过渡期历史数据可能是 username，两种键都尝试解析
    creator = db_manager.get_user_by_id(created_by) or db_manager.get_user(created_by)
    if not creator:
        raise HTTPException(status_code=404, detail="创建人用户记录不存在")

    success = db_manager.update_user(creator['id'], name=new_name)
    if not success:
        raise HTTPException(status_code=500, detail="更新创建人姓名失败")

    # 操作日志 + 系统评论（与派单/改派一致，记录操作与操作人）
    token = current_user.get('token') or ''
    try:
        _role = get_role_prefix(getattr(ticket, 'created_by', None), getattr(ticket, 'assigned_to', None), username)
        await OperationLogService.log(
            db=db,
            task_id=task_id,
            op_type=OperationType.UPDATE,
            operator=username,
            operator_name=user_name,
            detail={"field": "created_by_name", "new_name": new_name},
            description=f"{_role}{user_name} 将创建人姓名更新为「{new_name}」" if _role else f"{user_name} 将创建人姓名更新为「{new_name}」",
        )
        await _add_system_comment(db, task_id, f"{user_name} 将创建人姓名更新为「{new_name}」", username, token)
    except Exception:
        pass

    return {"name": new_name, "created_by": created_by}


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

    - 可携带用户倾向的派单人（preferred_assignee，users.id），派单流水线会将其作为强加权信号
      （复用 assigner 既有的 preferred_assignee 字段，见 TicketContext.preferred_assignee）。
    - 实现：清空 assigned_to + 状态回 new + 写入 metadata_info.preferred_assignee，
      再向 Redis 发布 usp:new_ticket 事件，由派单 Worker 立即重新派单（发布失败则依赖定时扫描兜底）。
    """
    import logging
    logger = logging.getLogger(__name__)

    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")

    is_admin = is_admin_user(current_user)
    username = actor_username(current_user)
    user_name = (current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", username)) or username
    token = current_user.get('token') if isinstance(current_user, dict) else getattr(current_user, "token", None)

    # 权限口径对齐 update_task：管理员 / 提单人 / 处理人 / 客户
    if not is_admin and not user_matches(current_user, ticket.assigned_to, ticket.customer, ticket.created_by):
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

    preferred = to_user_id((payload.preferred_assignee or "").strip()) or (payload.preferred_assignee or "").strip()
    if not preferred:
        raise HTTPException(status_code=400, detail="请选择倾向处理人")
    remark = (payload.remark or "").strip()

    # 复位前捕获旧值，供操作日志角色判定（复位后 assigned_to 已清空）
    created_by = ticket.created_by
    old_assigned_to = ticket.assigned_to

    # 重置派单状态：清空处理人 + 状态回 new
    ticket.assigned_to = None
    ticket.status = TicketStatus.NEW

    # 写入用户倾向派单人；派单详情已不再写 metadata_info（统一走 task_dispatch_log，见 §4.2/§九-M1），
    # 此处 pop 仅用于清理历史遗留的旧派单元数据（worker 已不再写入这些键）
    meta = dict(ticket.metadata_info or {})
    meta["preferred_assignee"] = preferred
    if remark:
        meta["preferred_assignee_remark"] = remark
    for k in ("assignee_name", "assignee_id", "assign_confidence",
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

        # 返回 object_path，前端可直接透传给建单接口的 attachments 字段，
        # 无需依赖进程内存 comment_attachment_map 的 temp_id 解析（跨进程/重启更稳）。
        return {"message": "上传附件成功", "object_path": object_path}
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

