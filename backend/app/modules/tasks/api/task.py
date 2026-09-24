"""tasks 任务管理 API（承接 fqa/ticket）。

MIGRATION.md 阶段 3：从 `app/modules/fqa/ticket/api/ticket.py` 搬迁而来，
路由前缀从 `/api/fqa/tickets` 迁移到 `/api/tasks`。

Wave 2.2 完成：工单(tickets)已升格为任务(tasks)，本模块使用统一的 Task/TaskComment 模型。
"""
import contextvars
import logging

from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File, Form, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.dialects.mysql import insert as mysql_insert
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
    TicketBatchCountRequest,
    TicketCreateNotificationRequest, RobotAlarmNotificationRequest, ProjectMemberResponse,
    # 代他人提单
    OnBehalfCandidate, ProxyRelationResponse, ProxyRelationDeclineRequest,
    TaskRelationCreate, TaskRelationResponse, TaskRelationBrief, BlockedTaskInfo
)
from app.modules.tasks.models.ticket import TicketStatus, TicketPriority, TicketType, RelationType
from app.modules.tasks.services.ticket_service import TicketService, convert_to_shanghai_time
from app.modules.tasks.services.proxy_relation_service import (
    ProxyRelationService,
    ProxyRelationError,
    ProxyRelationStatus,
)
from app.core.ticket_roles import (
    resolve_ticket_roles,
    get_ticket_roles as core_get_ticket_roles,
    load_relation as core_load_relation,
    SIDE_ASSIGNED,
    SIDE_CREATOR,
)
from app.modules.tasks.services.operation_log_service import OperationLogService, get_role_prefix
from app.models.task import OperationType, TaskStep, TaskFollower, TaskParticipant, Task, TaskRelation
from app.modules.tasks.services.task_policy_service import get_all_policies
from app.modules.tasks.api.ws import (
    ws_broadcast_comment,
    ws_broadcast_comment_deleted,
    ws_broadcast_task_updated,
    ws_broadcast_read_receipt,
    manager,
)
from app.utils.minio_client import minio_client
from app.utils.notification_utils import NotificationUtils, _format_shanghai
from app.integrations.api import verify_sync_api_key, verify_robot_alarm_api_key
from app.services.identity_service import IdentityService
from app.core.config import settings
from app.core.user_identity import user_matches, is_admin_user, to_user_id, actor_username, identity_keys
from app.services.redispatch_tip_service import (  # 派单说明：列表/气泡/详情同一出口
    build_redispatch_tip,
    clean_reasoning_for_display,
    step0_blocks_redispatch,
)

router = APIRouter(tags=["tasks"])

# 模块级 logger（避免每个端点内重复 logging.getLogger(__name__)）
logger_task = logging.getLogger(__name__)

# 状态中文映射（用于操作日志描述）
STATUS_LABEL = {
    "new": "待处理",
    "in_progress": "处理中",
    "pending": "已挂起",
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


# 注：派单说明（tip_detail）唯一出口见 app.services.redispatch_tip_service.build_redispatch_tip


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
    """返回当前操作人属于接单人侧(assigned) 还是 提单人侧(creator)，都不是则 None。

    代提改造后优先读请求级角色缓存（由 `get_ticket_roles` 写入）：
    被代理人（已确认跟进）归 creator 侧（与代理人同侧，代表问题方），
    pending 期间不参与协商故为 None。缓存未命中时退化为原双键比较。

    安全：缓存以「操作人 + task_id」为键（见 `_roles_cache_key`），
    避免同一请求上下文内解析过他人角色后被复用（越权风险）。
    """
    roles = (_roles_cache.get() or {}).get(_roles_cache_key(current_user, getattr(ticket, "id", None)))
    if roles is not None:
        return roles.side
    if user_matches(current_user, getattr(ticket, 'assigned_to', None)):
        return _ACTOR_SIDE_ASSIGNED
    if user_matches(current_user, getattr(ticket, 'created_by', None)):
        return _ACTOR_SIDE_CREATOR
    if username and username and username == getattr(ticket, 'assigned_to', None):
        return _ACTOR_SIDE_ASSIGNED
    if username and username == getattr(ticket, 'created_by', None):
        return _ACTOR_SIDE_CREATOR
    return None


# 请求级角色缓存：键为 (操作人标识, task_id)，值 TicketRoles。
# 用 ContextVar 而非全局 dict，避免并发请求间串数据（安全：仅缓存本请求内已鉴权结果）。
_roles_cache: "contextvars.ContextVar[Optional[Dict[Any, Any]]]" = contextvars.ContextVar(
    "ticket_roles_cache", default=None
)


def _roles_cache_key(current_user: Any, task_id: Optional[int]) -> Any:
    """角色缓存键：必须含操作人标识。

    只按 task_id 缓存会在「同一请求内先以 A 身份解析、再问 B 的角色」时返回 A 的结果，
    造成越权误判；加上操作人双键（id + username）后天然隔离。
    """
    if not task_id:
        return None
    if isinstance(current_user, dict):
        uid = current_user.get("id") or current_user.get("user_id")
        uname = current_user.get("username")
    else:
        uid = getattr(current_user, "id", None)
        uname = getattr(current_user, "username", None)
    return (str(uid or ""), str(uname or ""), task_id)


async def get_ticket_roles(
    db: AsyncSession,
    ticket,
    current_user: Dict[str, Any],
    *,
    with_follower: bool = False,
):
    """本模块角色解析入口（委托 `app/core/ticket_roles.get_ticket_roles`）。

    额外做两件事：
    1. 结果按 (操作人, task_id) 缓存在请求级 ContextVar，同一请求多处判定不重复查库；
    2. `_actor_side` 读该缓存，从而让被代理人归 creator 侧（决策 7）。
    """
    if ticket is None:
        return None
    task_id = getattr(ticket, "id", None)
    cache_key = _roles_cache_key(current_user, task_id)

    # 命中缓存直接返回（with_follower 场景需真实关注态，不缓存）
    cache = _roles_cache.get()
    if not with_follower and cache and cache_key and cache_key in cache:
        return cache[cache_key]

    is_follower = False
    if with_follower and task_id:
        try:
            from app.modules.tasks.models.ticket import TaskFollower
            from sqlalchemy import select as _select
            me_keys = list(identity_keys(actor_username(current_user)))
            follower = await db.execute(
                _select(TaskFollower.id)
                .where(TaskFollower.task_id == task_id, TaskFollower.username.in_(me_keys))
                .limit(1)
            )
            is_follower = follower.first() is not None
        except Exception as e:
            logger_task.warning(f"读取关注关系失败 task_id={task_id}: {e}")

    roles = await core_get_ticket_roles(db, ticket, current_user, is_follower=is_follower)

    if cache is None:
        cache = {}
        _roles_cache.set(cache)
    if cache_key:
        cache[cache_key] = roles
    return roles


async def _load_relation(db: AsyncSession, task_id: int):
    """读取工单的代理关系（无关系返回 None）。失败不阻断主流程。"""
    return await core_load_relation(db, task_id)


def _apply_step_update_meta(ticket, current_user, username: str) -> Dict[str, Any]:
    """记录当前 step 更新元信息，并按对手回应规则累加回合。

    返回 dict：{bump_round: bool, round_reached_max: bool}，供调用方决定是否发软提醒。
    """
    side = _actor_side(ticket, current_user, username)
    prev_side = getattr(ticket, 'step_last_updated_by', None)

    # 对手回应：前一方有记录且与当前不同，加 1 回合；
    # 首次协商（无任何一方记录）也视为开启第 1 回合
    bump_round = False
    if side and (not prev_side or prev_side != side):
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
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    import logging
    logger = logging.getLogger(__name__)

    try:
        logger.debug(f"开始复合过滤查询任务列表, filters_count={len(filter_request.filters) if filter_request.filters else 0}, page={filter_request.page}, size={filter_request.size}")

        auth_header = request.headers.get("Authorization")
        token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

        # 当前用户 username：供「我关注的」(followedBy) 过滤与列表 is_followed 回填
        current_username = actor_username(current_user)

        result = await TicketService.filter_tickets(db, filter_request, token, current_username)
        logger.debug(f"复合过滤查询任务列表成功, total={result.get('total', 0)}")
        return result
    except Exception as e:
        logger.error(f"复合过滤查询任务列表失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"复合过滤查询任务列表失败: {str(e)}")


@router.post("/filter/counts", response_model=List[int])
async def filter_tasks_counts(
    batch_request: TicketBatchCountRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """批量计数：每组 queries 独立统计 total，一次网络往返返回多组角标数。

    供系统任务页「全部/项目相关/待我处理/与我相关/我关注的」分类角标使用，
    替代前端并发多次 POST /filter（减少认证/中间件开销与连接占用）。
    """
    try:
        auth_header = request.headers.get("Authorization")
        token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

        # 当前用户 username：供「我关注的」(followedBy) 角标计数
        current_username = actor_username(current_user)

        totals = []
        for q in batch_request.queries:
            totals.append(await TicketService.count_tickets(db, q, token, current_username))
        return totals
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"批量计数失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"批量计数失败: {str(e)}")


@router.post("/{task_id}/follow", response_model=dict)
async def follow_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """关注工单（卡片星标）。幂等：重复关注只刷新 created_at，不报错。

    归属人由服务端 token 解析（actor_username），前端无法替他人关注。
    """
    exists = await db.execute(select(Task.id).where(Task.id == task_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="任务未找到")

    username = actor_username(current_user)
    # MySQL INSERT ... ON DUPLICATE KEY UPDATE：多端并发点星标不撞唯一键回滚
    stmt = mysql_insert(TaskFollower).values(task_id=task_id, username=username)
    stmt = stmt.on_duplicate_key_update(created_at=func.now())
    await db.execute(stmt)
    await db.commit()

    return {"ok": True, "task_id": task_id, "followed": True}


@router.delete("/{task_id}/follow", response_model=dict)
async def unfollow_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """取消关注（取消星标）。幂等：未关注时返回 0 行影响，不报错。"""
    username = actor_username(current_user)
    await db.execute(
        delete(TaskFollower).where(
            TaskFollower.task_id == task_id,
            TaskFollower.username == username,
        )
    )
    await db.commit()

    return {"ok": True, "task_id": task_id, "followed": False}


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


# ⚠️ 静态路径端点必须声明在下面的贪婪路径 GET /{task_id} 之前：
# FastAPI 按注册顺序匹配，`GET /tasks/{task_id}`（task_id: int）会抢先吃下
# `/tasks/on-behalf-candidates`，int 解析失败 → 422 int_parsing（path.task_id）。
@router.get("/on-behalf-candidates", response_model=List[OnBehalfCandidate])
async def get_on_behalf_candidates(
    project_id: Optional[str] = Query(None, description="项目ID，用于把同项目人员排在前面"),
    keyword: Optional[str] = Query(None, description="按姓名 / username 模糊过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """代他人提单选人候选（见设计文档 §3.5）。

    为什么不复用现有接口：
      - `GET /api/tasks/{id}/project-members` 需要 task_id —— 提单时工单尚不存在；
      - `GET /api/tasks/assignable-users` 无项目参数。

    返回：在职（users.status='active'）用户，`group='project'` 段在前、`'all'` 段在后。
    **分组标记必须显式返回**：现有 project-members 的 role_name 在项目成员段可能为空，
    前端无法靠它区分分组，只能靠位置猜，很脆。
    """
    try:
        from app.core.db import SessionLocal
        from app.models.identity import UserDB

        me_id = to_user_id(current_user.get("id") if isinstance(current_user, dict) else None)
        me_keys = set(identity_keys(actor_username(current_user))) | ({me_id} if me_id else set())

        kw = (keyword or "").strip().lower()

        def _query(pid: Optional[str]):
            sync_db = SessionLocal()
            try:
                project_user_ids: set = set()
                if pid:
                    try:
                        members = db_manager.get_project_members(pid, include_usp=False) or []
                        for m in members:
                            for key in ("id", "username"):
                                val = (m.get(key) or "").strip()
                                if val:
                                    project_user_ids.add(val)
                    except Exception as exc:
                        logger_task.warning(f"代提选人：取项目成员失败 project_id={pid}: {exc}")

                users = (
                    sync_db.query(UserDB)
                    .filter(UserDB.status == "active")
                    .all()
                )
                return project_user_ids, [
                    {
                        "id": (u.id or "").strip(),
                        "username": (u.username or "").strip(),
                        "name": u.name,
                    }
                    for u in users
                ]
            finally:
                sync_db.close()

        project_user_ids, users = await run_in_threadpool(_query, project_id)

        def _sort_key(u: Dict[str, Any]):
            return (u.get("name") or u.get("username") or "").lower()

        project_items: List[OnBehalfCandidate] = []
        other_items: List[OnBehalfCandidate] = []

        for u in sorted(users, key=_sort_key):
            uid = u["id"]
            uname = u["username"]
            if not uid or not uname:
                continue
            # 排除自己（不给自己代提）
            if uid in me_keys or uname in me_keys:
                continue
            if kw and kw not in (u.get("name") or "").lower() and kw not in uname.lower():
                continue
            in_project = bool(
                project_user_ids and (uid in project_user_ids or uname in project_user_ids)
            )
            item = OnBehalfCandidate(
                id=uid, username=uname, name=u.get("name") or uname,
                group="project" if in_project else "all",
            )
            (project_items if in_project else other_items).append(item)

        return project_items + other_items
    except HTTPException:
        raise
    except Exception as e:
        logger_task.error(f"代提选人查询失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="代提选人查询失败")


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

        # 代他人提单（代理提单）：详情接口回填代理关系字段（口径与列表一致：
        # 视角标记按 token 身份判定，姓名仅参与人可见）——
        # 作为详情页「关系横幅」独立接口的兜底，也让代理关系在全链路可审计。
        try:
            from app.core.security import decode_token as _decode_token
            _payload = _decode_token(token) if token else None
            _me_username = (_payload or {}).get("sub")
            _user_map = await TicketService._get_user_map(token)
            await TicketService._attach_proxy_relations(db, [ticket], _user_map, _me_username)
        except Exception as proxy_err:
            logger.warning(f"代理关系回填失败 task_id={task_id}: {proxy_err}")
        
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
            # 权限控制：派单理由相关属较敏感信息，按下述身份矩阵返回，避免无关查看者拿到：
            #   - result.reasoning（"为什么派给他"）→ 仅「接单人(assigned_to) 或 管理员」可见
            #   - result.tip_detail（重派高情商话术）→ 仅「提单人(created_by) 或 管理员」可见
            # （接单人身份依据 _log.assigned_id（本轮真正被派单对象）判定，见下 `_viewer_assignee`）
            _viewer_user = None
            _viewer_creator = False
            _viewer_admin = False
            try:
                from app.core.database import get_user_with_roles
                from app.core.user_identity import user_matches, is_admin_user
                from app.core.security import decode_token
                if token:
                    _payload = decode_token(token)
                    _uname = (_payload or {}).get("sub")
                    if _uname:
                        _viewer_user = get_user_with_roles(_uname)
                        if _viewer_user:
                            _viewer_creator = user_matches(_viewer_user, getattr(ticket, "created_by", None))
                            _viewer_admin = is_admin_user(_viewer_user)
            except Exception:
                _viewer_user = None
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
                # 接单人身份：当前登录者 == 本轮真正被派单对象（_log.assigned_id）时可看「派单理由」
                _viewer_assignee = bool(_viewer_user and user_matches(_viewer_user, _log.assigned_id))
                # 面向用户展示的派单理由：把 reasoning 里可能残留的 users.id 替换为姓名
                # （供 tip_detail 话术与接单人/管理员的「派单理由」共用）
                reasoning_display = clean_reasoning_for_display(_log.reasoning, _log, user_map)
                # 列表 / 气泡 / 详情同一出口（未派到倾向人走详情模板，Step0 走短句）
                tip_detail = build_redispatch_tip(_log, user_map)
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
                        # 派单理由（为什么派给他）仅对「接单人」或「管理员」可见；其余查看者不返回
                        # （前端据此展示给被派单工程师；提单人看 tip_detail 已含原因，无需重复）
                        "reasoning": reasoning_display if (_viewer_assignee or _viewer_admin) else None,
                        "profile": {
                            "dept": prof.get("dept"),
                            "job_level": prof.get("job_level"),
                            "modules": prof.get("modules"),
                            "duty": prof.get("duty"),
                            "missing": prof.get("missing") or [],
                            "specified_name": (prof.get("specified_name") or "").strip() or None,
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


@router.get("/my-followups/count")
async def get_my_followups_count(
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """「待我跟进」角标数：我是被代理人、关系 pending、工单未终结。"""
    try:
        me_id = to_user_id(current_user.get("id") if isinstance(current_user, dict) else None) \
            or to_user_id(actor_username(current_user))
        if not me_id:
            return {"count": 0}
        count = await ProxyRelationService.count_pending_for_principal(db, me_id)
        return {"count": count}
    except Exception as e:
        logger_task.error(f"待我跟进计数失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="待我跟进计数失败")


@router.get("/{task_id}/proxy-relations", response_model=List[ProxyRelationResponse])
async def get_proxy_relations(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """查询工单的代理关系（仅参与人可见，非参与人返回 403）。

    安全：先解析角色集合，非参与人一律 403，避免泄露「谁代谁提单」。
    """
    try:
        relation = await ProxyRelationService.get_task_relation(db, task_id)
        if not relation:
            return []

        ticket = await TicketService.get_ticket_by_id(db, task_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="任务未找到")

        roles = resolve_ticket_roles(ticket, current_user, relation)
        if not roles.is_related:
            raise HTTPException(status_code=403, detail="无权查看该工单的代理关系")

        return [_proxy_relation_response(relation, roles)]
    except HTTPException:
        raise
    except Exception as e:
        logger_task.error(f"查询代理关系失败 task_id={task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="查询代理关系失败")


@router.post("/{task_id}/proxy-relations/{relation_id}/ack", response_model=ProxyRelationResponse)
async def ack_proxy_relation(
    task_id: int,
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """被代理人确认跟进（pending → acknowledged，获协办权）。

    安全：**仅被代理人本人**可调用，以 token 身份为准，不信任前端传入的 principal_id。
    """
    return await _handle_relation_action(db, task_id, relation_id, current_user, action="ack")


@router.post("/{task_id}/proxy-relations/{relation_id}/decline", response_model=ProxyRelationResponse)
async def decline_proxy_relation(
    task_id: int,
    relation_id: int,
    payload: ProxyRelationDeclineRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """被代理人拒绝（与我无关，pending → declined，需填原因）。

    工单不中断：代理人继续兜底推进，其 created_by 身份不受影响。
    """
    return await _handle_relation_action(
        db, task_id, relation_id, current_user, action="decline", remark=payload.remark
    )


def _proxy_relation_response(relation, roles) -> ProxyRelationResponse:
    """组装关系响应（含当前用户视角标记与参与人姓名，便于前端直接渲染）。"""
    user_map = {}
    try:
        from app.services.user_service import user_service
        user_map = user_service.get_user_map() or {}
    except Exception:
        pass

    agent_id = getattr(relation, "agent_id", None) or ""
    principal_id = getattr(relation, "principal_id", None) or ""

    return ProxyRelationResponse(
        id=relation.id,
        task_id=relation.task_id,
        relation_status=relation.relation_status,
        source=getattr(relation, "source", None),
        remark=getattr(relation, "remark", None),
        is_agent=roles.is_agent,
        is_principal=roles.is_principal or roles.is_pending_principal,
        is_assignee=roles.is_assignee,
        agent_name=user_map.get(agent_id) or getattr(relation, "agent_username", None) or agent_id,
        principal_name=user_map.get(principal_id) or getattr(relation, "principal_username", None) or principal_id,
        notified_at=relation.notified_at,
        acked_at=relation.acked_at,
        declined_at=relation.declined_at,
        created_at=relation.created_at,
    )


async def _handle_relation_action(
    db: AsyncSession,
    task_id: int,
    relation_id: int,
    current_user: Dict[str, Any],
    *,
    action: str,
    remark: str = "",
) -> ProxyRelationResponse:
    """关系动作公共实现（ack / decline）：身份 + 归属双校验 + 审计。"""
    try:
        ticket = await TicketService.get_ticket_by_id(db, task_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="任务未找到")

        relation = await ProxyRelationService.get_by_id(db, relation_id)
        if not relation or relation.task_id != task_id:
            raise HTTPException(status_code=404, detail="代理关系未找到")

        # 归属校验：仅被代理人本人
        from app.core.user_identity import user_matches as _user_matches
        if not _user_matches(
            current_user, relation.principal_id, relation.principal_username
        ):
            logger_task.warning(
                f"越权访问代理关系 task_id={task_id} relation_id={relation_id} "
                f"operator={actor_username(current_user)}"
            )
            raise HTTPException(status_code=403, detail="无权操作该代理关系")

        operator_username = actor_username(current_user)

        if action == "ack":
            await ProxyRelationService.acknowledge(db, relation, operator_username)
            log_desc = f"【被代理人】{operator_username} 确认跟进本工单"
            notify_action, notify_reason = "acked", ""
        else:
            await ProxyRelationService.decline(db, relation, operator_username, remark)
            log_desc = f"【被代理人】{operator_username} 表示与本单无关：{relation.remark}"
            notify_action, notify_reason = "declined", relation.remark or ""

        # 审计日志（与工单操作日志同表，参与人可见）
        try:
            await OperationLogService.log(
                db=db,
                task_id=task_id,
                op_type=OperationType.UPDATE,
                operator=operator_username,
                operator_name=current_user.get("name") if isinstance(current_user, dict) else None,
                description=log_desc,
            )
        except Exception as exc:
            logger_task.warning(f"写代理关系操作日志失败 relation_id={relation_id}: {exc}")

        await db.commit()
        await db.refresh(relation)

        # 通知代理人（失败不影响状态流转）
        try:
            user_map = await TicketService._get_user_map(None)
            agent_name = user_map.get(relation.agent_id) or relation.agent_username or relation.agent_id
            principal_name = user_map.get(relation.principal_id) or relation.principal_username or relation.principal_id
            await NotificationUtils.send_proxy_relation_notification(
                ticket_id=task_id,
                title=ticket.title or "",
                project_name=ticket.project_name or "",
                agent_name=agent_name,
                principal_name=principal_name,
                action=notify_action,
                reason=notify_reason,
                user_names=[relation.agent_id],
                token=current_user.get("token") if isinstance(current_user, dict) else None,
            )
        except Exception as exc:
            logger_task.warning(f"代提关系变更通知失败 relation_id={relation_id}: {exc}")

        # WS 事件驱动前端角标 / 横幅刷新（新增事件名，不改既有事件）
        try:
            from app.modules.tasks.api.ws import ws_broadcast_proxy_relation
            await ws_broadcast_proxy_relation(
                task_id=task_id,
                relation_id=relation.id,
                relation_status=relation.relation_status,
                principal_id=relation.principal_id,
                agent_id=relation.agent_id,
            )
        except Exception as exc:
            logger_task.warning(f"代理关系 WS 广播失败 relation_id={relation_id}: {exc}")

        roles = resolve_ticket_roles(ticket, current_user, relation)
        return _proxy_relation_response(relation, roles)
    except ProxyRelationError as e:
        await db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger_task.error(f"代理关系操作失败 task_id={task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="代理关系操作失败")


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
    # 统一角色解析（含被代理人）：见 app/core/ticket_roles.py
    roles = await get_ticket_roles(db, ticket, current_user)
    if not is_admin and not can_operate:
        if not roles.is_related:
            raise HTTPException(status_code=403, detail="无权限更新此任务")
        if ticket.status == TicketStatus.CLOSED:
            raise HTTPException(status_code=400, detail="已关闭的任务不能更新")
        if ticket_update.status:
            if ticket.status == TicketStatus.NEW and not roles.is_creator:
                raise HTTPException(status_code=400, detail="只允许创建者开始任务！")
            if ticket.status in [TicketStatus.PENDING, TicketStatus.IN_PROGRESS] and not roles.is_assignee:
                raise HTTPException(status_code=400, detail="只允许处理人更新任务！")
            # 关单（resolved →）：决策 6 —— 由 created_by（代理人）+ 已确认被代理人 + 管理员判定，
            # customer 收敛为纯展示「联系人」，不再参与权限（修掉「新单 customer 为空导致非 admin 关不掉单」）
            if ticket.status == TicketStatus.RESOLVED and not roles.can_close:
                raise HTTPException(status_code=400, detail="只允许发起人或被代理人确认已解决任务！")

    try:
        token = current_user.get('token')
        from_assignee = (getattr(ticket, "assigned_to", None) or "").strip()
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
        update_data = ticket_update.model_dump(
            exclude={'operation_type', 'reassign_kind', 'reassign_reason'},
            exclude_unset=True,
        )
        for key, value in update_data.items():
            if value is not None and key != 'status':
                changed_fields.append(key)
        
        if op_type_str == 'escalate':
            # 升级上报：escalate_count +1，协商回合清零，且升级后不再受回合上限限制
            # （处理人可一锤定音直接设置节点时间；双方仍可继续协商但不卡回合）
            prev_count = int(getattr(ticket, 'escalate_count', 0) or 0)
            ticket.escalate_count = prev_count + 1
            ticket.step_negotiation_round = 0
            ticket.curr_step_agreed = False
            # 节点结束时间对齐当前工单截止时间（升级时以工单 deadline 为准）
            ticket.curr_step_endtime = ticket.deadline_at
            # 回合归属：记录升级操作方侧标识 → 轮到另一方响应
            # （提单人升级 → step_last_updated_by='creator'，轮到处理人一锤定音）
            esc_side = _actor_side(ticket, current_user, username)
            if esc_side:
                ticket.step_last_updated_by = esc_side
                ticket.step_last_updated_at = func.now()
            await OperationLogService.log(
                db=db, task_id=task_id, op_type=OperationType.ESCALATE,
                operator=username, operator_name=user_name,
                detail={
                    "escalate_count": prev_count + 1,
                    "round_reset": 0,
                    "actor_side": esc_side,
                    "curr_step_endtime": ticket.curr_step_endtime.isoformat() if ticket.curr_step_endtime else None,
                },
                description=f"{_role}{user_name} 升级了工单（第{prev_count + 1}次），协商不再受回合上限限制" if _role else f"{user_name} 升级了工单（第{prev_count + 1}次），协商不再受回合上限限制",
            )
            await _add_system_comment(db, task_id, f"{user_name} 升级了工单（第{prev_count + 1}次），协商不再受回合上限限制", username, token)
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
            kind = (getattr(ticket_update, "reassign_kind", None) or "").strip()
            if kind not in ("misassign", "stage", "other"):
                kind = ""
            reason = (getattr(ticket_update, "reassign_reason", None) or "").strip()
            kind_label = {"misassign": "派错了", "stage": "阶段转派", "other": "其它"}.get(kind, "")
            detail = {
                "new_assignee": new_assignee,
                "from_assignee": from_assignee,
            }
            if kind:
                detail["kind"] = kind
            if reason:
                detail["reason"] = reason
            desc = f"{_role}{user_name} 将工单重新指派给 {new_assignee_name}" if _role else f"{user_name} 将工单重新指派给 {new_assignee_name}"
            if kind_label:
                desc = f"{desc}（{kind_label}）"
            await OperationLogService.log(
                db=db, task_id=task_id, op_type=OperationType.REASSIGN,
                operator=username, operator_name=user_name,
                detail=detail,
                description=desc,
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
        roles = await get_ticket_roles(db, ticket, current_user)
        if not (roles.is_assignee or roles.is_creator):
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

        # ── 写入参与人（幂等 upsert） ──
        # 评论/附件是参与工单的核心行为，成功后把当前用户记入 task_participants。
        # 同事务：评论失败则参与人不写入，原子性保证。
        # ON DUPLICATE KEY UPDATE 刷新 last_active_at，重复评论只更新时间不产生脏数据。
        participant_stmt = mysql_insert(TaskParticipant).values(
            task_id=task_id, username=username,
        ).on_duplicate_key_update(last_active_at=func.now())
        await db.execute(participant_stmt)

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
                    "new": "待处理", "in_progress": "处理中", "pending": "已挂起",
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

    # 统一角色解析（含被代理人）：与 respond / reopen-step 同口径。
    # 原双键直比 created_by/assigned_to 会漏掉「已确认跟进的被代理人」，
    # 使其看得见工单却关不掉（can_close 已放行，接口却 403，两处口径打架）。
    _roles = await get_ticket_roles(db, ticket, current_user)

    # AI 工单（source='ai'）允许任何登录用户操作状态（created_by='system' 不是真实用户）
    if ticket.source == 'ai':
        pass
    elif not (
        _roles.is_creator or _roles.is_assignee or _roles.is_principal or is_admin
    ):
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

        # ── 预加载工单关联策略（阻塞检查 + 重复同步共用一次读取） ──
        try:
            policies = await get_all_policies(db)
        except Exception:
            policies = None  # 自动退化为默认值

        # ── 前置/子任务阻塞校验 ──
        blocked = await _check_relation_block(db, task_id, ticket.status, status_enum, policies=policies)
        if blocked:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "blocked_by_related_tasks",
                    "message": "当前工单存在未完成的前置工单或子任务，无法完成/关闭",
                    "blocked": [b.model_dump() for b in blocked],
                }
            )

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

        # ── 重复工单状态同步（受 policies.duplicate_status_sync_enabled 开关控制） ──
        try:
            synced_cnt = await _sync_duplicate_status(
                db=db,
                source_task_id=task_id,
                new_status=status_enum,
                actor_name=username,
                policies=policies,
                token=token,
                resolution_summary=resolution_summary,
            )
            if synced_cnt > 0:
                await _add_system_comment(
                    db, task_id,
                    f"已自动同步 {synced_cnt} 个重复工单的状态",
                    username, token,
                )
        except Exception as sync_err:
            # 同步失败不阻塞主流程：记录日志即可
            import logging as _logging
            _logging.getLogger(__name__).warning("重复工单状态同步失败: %s", sync_err)

        # _add_system_comment 的 commit 会使 updated_ticket 的 comments 关系过期，
        # 需重新查询以避免 FastAPI 序列化时触发异步外的懒加载（MissingGreenlet）
        return await _reload_ticket_with_comments(db, task_id)
    except HTTPException:
        # 业务性拒绝（403 撤回收窄 / 400 缺少解决方式 / 422 前置阻塞）需原样透出，
        # 否则会被下方兜底 except 重新包成 500，前端拿不到可展示的原因。
        raise
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
    ticket_type: Optional[str] = Query(None, alias="type", description="指定工单类型（可选）。传入时优先按此值查模板；不传则取 task_id 对应工单的 ticket_type"),
):
    """读取 task_steps 模板（按工单 task_type 过滤，sequence 升序）。

    前端「工单阶段性处理」区域据此生成当前节点描述（如 1/3 进度）。
    子任务创建弹窗在用户切换工单类型时，也传 type=xxx 按新类型重拉阶段列表。
    """
    from app.models.task import TaskType
    # 优先使用前端显式传入的 type；否则按 task_id 反查父工单的 ticket_type
    effective_type: Optional[str] = None
    if ticket_type:
        try:
            TaskType(ticket_type.strip())  # 校验合法性
            effective_type = ticket_type.strip()
        except ValueError:
            effective_type = None
    if effective_type is None:
        ticket = await TicketService.get_ticket_by_id(db, task_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="任务未找到")
        effective_type = ticket.task_type
    result = await db.execute(
        select(TaskStep)
        .where(TaskStep.task_type == effective_type)
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
    # 统一角色解析（含被代理人）：**必须早于下方 _actor_side 调用**——
    # 命中请求级缓存后 side 才识别得出「已确认跟进的被代理人」属 creator 侧；
    # 否则 fallback 双键比较不认识被代理人，回合归属会丢失（既不错记也不记录）。
    _roles = await get_ticket_roles(db, ticket, current_user)

    # AI 工单（created_by='system'）允许任何登录用户操作；
    # 其余需处理人/提单人/被代理人(已确认跟进)/管理员/操作权限
    if ticket.source == 'ai':
        pass
    elif not (
        _roles.is_assignee or _roles.is_creator or _roles.is_principal or is_admin or can_operate
    ):
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


class CompleteStepRequest(BaseModel):
    """当前阶段完成请求：处理人选择下一阶段节点并设置其结束时间。"""
    next_step_id: int = Field(..., description="下一阶段节点ID（必须为同 task_type 的节点）")
    curr_step_endtime: datetime = Field(..., description="下一阶段节点结束时间（ISO 字符串，naive UTC 存库）")


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
    roles = await get_ticket_roles(db, ticket, current_user)

    # 权限：仅接单人 / 管理员 / operate 权限位（与前端 isAssignee 门控、set-step-time 口径一致）。
    # ⚠️ 不可回填 `roles.can_operate`：该字段在 resolve_ticket_roles 内被 OR 上了
    # is_creator / is_principal，会把提单人、已确认跟进的被代理人一并放行，
    # 使其可绕过前端替处理人推进阶段并设定下一阶段 SLA（接口越权 + 打乱协商回合）。
    if ticket.source == 'ai':
        pass
    elif not (roles.is_assignee or is_admin or can_operate):
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

    # 权限：接单人 / 提单人 / 被代理人(已确认跟进) / 管理员 / 操作权限 均可协商（回合双方对话）
    # 决策 10：被代理人在 pending 期间**不参与协商**（is_principal 仅在 acknowledged 为 True）
    _roles = await get_ticket_roles(db, ticket, current_user)
    if ticket.source != 'ai' and not (
        _roles.is_assignee or _roles.is_creator or _roles.is_principal or is_admin or can_operate
    ):
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
    action_desc = f"预期「{ticket.curr_step_name}」时间（{endtime_label}）"
    if step_changed:
        action_desc += f"，节点由「{old_step_name}」调整为「{ticket.curr_step_name}」"
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
        description=f"{_role}{user_name} {action_desc}。缘由：{reason}" if _role else f"{user_name} {action_desc}。缘由：{reason}",
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
    comment_lines = [f"{user_name} {action_desc}。缘由：{reason}"]
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

    # 仅处理人/管理员/操作权限（角色口径统一走 roles，且早于 _actor_side 调用以命中缓存）
    _roles = await get_ticket_roles(db, ticket, current_user)
    _is_assignee = _roles.is_assignee
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
    # 一锤定音由谁按下就归谁的侧别（已确认跟进的被代理人亦归 creator 侧）；
    # 纯管理员/运维等非参与者 fallback 为接单人侧（语义：轮到问题方确认）
    ticket.step_last_updated_by = _actor_side(ticket, current_user, username) or _ACTOR_SIDE_ASSIGNED
    ticket.step_last_updated_at = func.now()
    await db.commit()
    await db.refresh(ticket)

    user_name = current_user.get('name', username) if isinstance(current_user, dict) else getattr(current_user, "name", None) or username
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
        f"{user_name} 设置节点时间为 {_format_shanghai(endtime)}（升级上报后一锤定音，不再协商）",
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

    # 权限：提单人 / 被代理人(已确认跟进) / 管理员 / 操作权限（AI 工单放行）
    _roles = await get_ticket_roles(db, ticket, current_user)
    if ticket.source != 'ai' and not (
        _roles.is_creator or _roles.is_principal or is_admin or can_operate
    ):
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
    # 打回提案按真实操作人侧别记录（已确认跟进的被代理人同属 creator 侧）；
    # 纯管理员/运维等非参与者 fallback 为 creator（打回语义即「轮到处理人确认」）
    ticket.step_last_updated_by = _actor_side(ticket, current_user, username) or _ACTOR_SIDE_CREATOR
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
    if not is_admin:
        _roles = await get_ticket_roles(db, ticket, current_user)
        if not _roles.is_assignee:
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


async def _step0_hit_blocks_redispatch(db: AsyncSession, ticket) -> Optional[str]:
    """首轮 Step0 已派上指定人则拦截重派（拼音/弱信号/指定多人同样拦截）。

    找不到人已走智能派单：首轮 matched_pref 不是 True，重派倾向人可以生效。
    展示名用首轮实际接单人，避免「张三、李四」只派了李四却提示张三。
    """
    from app.models.task_dispatch_log import TaskDispatchLog
    from app.models.identity import UserDB

    first = (await db.execute(
        select(TaskDispatchLog)
        .where(TaskDispatchLog.task_id == ticket.id)
        .order_by(TaskDispatchLog.dispatch_round.asc())
        .limit(1)
    )).scalars().first()
    blocked_id = step0_blocks_redispatch(first)
    if not blocked_id:
        return None
    row = (await db.execute(select(UserDB).where(UserDB.id == blocked_id))).scalars().first()
    return ((row.name if row else None) or blocked_id)


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

    # 权限口径对齐 update_task：管理员 / 提单人 / 处理人 / 被代理人(已确认跟进)
    # customer 收敛为展示用联系人，不再参与权限（决策 6）
    _roles = await get_ticket_roles(db, ticket, current_user)
    if not is_admin and not (_roles.is_assignee or _roles.is_creator or _roles.is_principal):
        raise HTTPException(status_code=403, detail="无权限重新派单此任务")
    if ticket.status == TicketStatus.CLOSED:
        raise HTTPException(status_code=400, detail="已关闭的任务不能重新派单")
    # 派单 Worker 只处理 source='ai' 的工单（manual 系统任务由兜底双工单直接指定处理人，不走 AI 派单），
    # 若允许 manual 工单重派，Worker 永远查不到它，会一直卡在「派单中」。
    if (ticket.source or "") != "ai":
        raise HTTPException(status_code=400, detail="该工单非智能派单工单，无法重新派单")
    # 首轮 Step0 已派上指定人：再派仍会被 Step0 盖掉，拦截。
    # 指定人找不到、已走智能派单：允许重派。
    _blocked = await _step0_hit_blocks_redispatch(db, ticket)
    if _blocked:
        raise HTTPException(
            status_code=400,
            detail=f"该工单已指定处理人「{_blocked}」，重新派单不会改变接单人",
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
    if old_assigned_to:
        meta["prev_assignee"] = old_assigned_to
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
        detail={"preferred_assignee": preferred, "remark": remark or None, "channel": "redispatch", "from_assignee": (old_assigned_to or "").strip() or None},
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


@router.post("/robot-alarm-notification")
async def send_robot_alarm_notification(
    body: RobotAlarmNotificationRequest,
    key: str = Depends(verify_robot_alarm_api_key),
):
    """设备报警提醒对外接口（供内部其他后端服务调用）。

    调用方直接传入报警字段，后端按 template.yaml 模板 10 组装后发送微信模板消息。
    鉴权仿企业微信 webhook，URL 携带 ?key=，需与后端 ROBOT_ALARM_API_KEY 一致。

    模板字段顺序：[报警机型, 设备编号, 报警原因, 告警级别, 告警时间]

    收件人解析（重要）：
      - `project_code` 实际是 `project.id`，先据此查 user_project_roles 拿到项目全部关联用户。
      - `users` 列表传入的不是 user.username，而是 user.external_credentials.usp.username 值；
        后端将 `users` 与项目成员的 usp.username 比对，命中者用其真实 user.username 发通知。
      - 未命中项目成员的入参会被丢弃（防止跨项目越权通知），并记录 warning 日志。
      - 全部未命中或项目无成员 → 400。
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        if not body.users:
            raise HTTPException(status_code=400, detail="users 不能为空")

        # 1. 通过 project_id (= project_code) 查询项目成员，含 external_credentials
        members = await run_in_threadpool(
            IdentityService.get_project_members,
            body.project_code,
            True,  # include_usp=True
        )
        if not members:
            raise HTTPException(
                status_code=404,
                detail=f"项目 {body.project_code} 无关联成员或项目不存在",
            )

        # 2. 构建 usp_username -> user.username 映射（仅项目成员）
        usp_to_username: Dict[str, str] = {}
        for m in members:
            ext = m.get("external_credentials") or {}
            if not isinstance(ext, dict):
                continue
            usp_username = (ext.get("usp") or {}).get("username") or ""
            usp_username = usp_username.strip()
            if usp_username and m.get("username"):
                usp_to_username[usp_username] = m["username"]

        # 3. 比对入参 users 与项目成员，得到真实通知目标 username
        requested = [u.strip() for u in body.users if u and u.strip()]
        recipients: List[str] = []
        unmatched: List[str] = []
        seen: set = set()
        for usp_name in requested:
            real_username = usp_to_username.get(usp_name)
            if real_username and real_username not in seen:
                seen.add(real_username)
                recipients.append(real_username)
            else:
                unmatched.append(usp_name)

        if unmatched:
            logger.warning(
                f"设备报警通知: project_code={body.project_code} 存在未匹配项目成员的入参 users={unmatched}，已丢弃"
            )

        if not recipients:
            raise HTTPException(
                status_code=400,
                detail=f"users 中无任何值匹配项目 {body.project_code} 成员的 external_credentials.usp.username",
            )

        # 4. 发送通知（传入真实 username，send_robot_alarm_notification 内部经 to_usernames 再归一）
        result = await NotificationUtils.send_robot_alarm_notification(
            robot_type=body.robot_type,
            robot_id=body.robot_id,
            content=body.content,
            level=body.level,
            start_time=body.start_time,
            user_names=recipients,
            token=None,
            project_code=body.project_code,
        )
        logger.info(
            f"设备报警通知已发送: project_code={body.project_code}, robot_type={body.robot_type}, "
            f"robot_id={body.robot_id}, level={body.level}, recipients={recipients}, unmatched={unmatched}"
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"发送设备报警通知失败 project_code={body.project_code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"发送设备报警通知失败: {str(e)}")


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


# ==================== 工单关联（task_relations） ====================


async def _check_relation_block(
    db: AsyncSession,
    task_id: int,
    current_status: TicketStatus,
    target_status: TicketStatus,
    policies: Optional[Dict[str, Any]] = None,
) -> List[BlockedTaskInfo]:
    """检查工单状态变更是否被前置工单或子任务阻塞（受 system_config 动态开关控制）。

    阻塞规则（默认值，可由管理员页面在线开关）：
      - in_progress → resolved：所有 predecessor 前置工单需已完成（resolved/closed）
      - resolved → closed：上述前置校验 + subtask 子工单需已完成（resolved/closed/canceled）

    policies: 可选，预加载的策略字典（避免每次查 DB）。若为 None 会自动从 system_config 读取。

    返回空列表表示无阻塞；返回 BlockedTaskInfo 列表表示被阻塞的工单清单。
    """
    blocked: List[BlockedTaskInfo] = []

    # 延迟读取配置
    if policies is None:
        try:
            policies = await get_all_policies(db)
        except Exception:
            # 配置读取失败时，退化为保守策略（默认全部开启阻塞）
            policies = {
                "block_predecessor_on_resolved": True,
                "block_predecessor_on_closed": True,
                "block_subtask_on_resolved": False,
                "block_subtask_on_closed": False,
            }

    # ── 前置工单阻塞 ──
    need_predecessor_check = (
        (target_status == TicketStatus.RESOLVED and policies.get("block_predecessor_on_resolved", True))
        or (target_status == TicketStatus.CLOSED and policies.get("block_predecessor_on_closed", True))
    )
    if need_predecessor_check:
        result = await db.execute(
            select(TaskRelation, Task).join(Task, Task.id == TaskRelation.target_task_id)
            .where(
                TaskRelation.source_task_id == task_id,
                TaskRelation.relation_type == RelationType.PREDECESSOR,
            )
        )
        for rel, pred_task in result.unique().all():
            pred_status = pred_task.status.value if hasattr(pred_task.status, 'value') else str(pred_task.status)
            if pred_status not in ('resolved', 'closed'):
                blocked.append(BlockedTaskInfo(
                    task_id=pred_task.id,
                    title=pred_task.title,
                    status=pred_status,
                    reason='predecessor',
                ))

    # ── 子工单阻塞（默认关闭：弱关联不阻塞任何状态流转） ──
    need_subtask_check = (
        (target_status == TicketStatus.RESOLVED and policies.get("block_subtask_on_resolved", False))
        or (target_status == TicketStatus.CLOSED and policies.get("block_subtask_on_closed", False))
    )
    if need_subtask_check:
        result = await db.execute(
            select(TaskRelation, Task).join(Task, Task.id == TaskRelation.target_task_id)
            .where(
                TaskRelation.source_task_id == task_id,
                TaskRelation.relation_type == RelationType.SUBTASK,
            )
        )
        for rel, sub_task in result.unique().all():
            sub_status = sub_task.status.value if hasattr(sub_task.status, 'value') else str(sub_task.status)
            if sub_status not in ('resolved', 'closed', 'canceled'):
                blocked.append(BlockedTaskInfo(
                    task_id=sub_task.id,
                    title=sub_task.title,
                    status=sub_status,
                    reason='subtask',
                ))

    return blocked


async def _sync_duplicate_status(
    db: AsyncSession,
    source_task_id: int,
    new_status: TicketStatus,
    actor_name: str,
    policies: Optional[Dict[str, Any]] = None,
    token: str = "",
    resolution_summary: Optional[str] = None,
) -> int:
    """将 source_task 的状态同步给所有 DUPLICATE 关联网单（一跳）。

    只有当 policies.duplicate_status_sync_enabled = true 时才执行。
    使用 session-level skip_sync 上下文变量防止 A→B→A 循环。
    每个被同步的工单都会写入操作日志 + 系统评论，并广播 WS。
    RESOLVED 状态会携带 resolution_summary（优先用传入值，否则用默认提示）。

    返回被同步的工单数。
    """
    if policies is None:
        try:
            policies = await get_all_policies(db)
        except Exception:
            return 0

    if not policies.get("duplicate_status_sync_enabled", False):
        return 0

    # ── 单次 SQL 查询：只取与 source_task_id 有 DUPLICATE 关系的一跳 ──
    from sqlalchemy import or_, and_
    stmt = (
        select(TaskRelation, Task)
        .join(
            Task,
            or_(
                and_(TaskRelation.source_task_id == source_task_id, Task.id == TaskRelation.target_task_id),
                and_(TaskRelation.target_task_id == source_task_id, Task.id == TaskRelation.source_task_id),
            ),
        )
        .where(
            TaskRelation.relation_type == RelationType.DUPLICATE,
            or_(
                TaskRelation.source_task_id == source_task_id,
                TaskRelation.target_task_id == source_task_id,
            ),
        )
    )
    result = await db.execute(stmt)

    targets: Dict[int, Task] = {}
    for rel, t in result.unique().all():
        if t and t.id != source_task_id:
            targets[t.id] = t

    if not targets:
        return 0

    new_status_val = new_status.value if hasattr(new_status, 'value') else str(new_status)

    synced_count = 0
    for tid, target_task in targets.items():
        if target_task.status == new_status:
            continue

        old_status_val = target_task.status.value if hasattr(target_task.status, 'value') else str(target_task.status)

        # 直接 ORM 更新状态——跳过 update_task_status 的额外校验（管理员显式开启的策略）
        target_task.status = new_status
        synced_count += 1

        # 写操作日志
        await OperationLogService.log(
            db=db,
            task_id=tid,
            op_type=OperationType.STATUS_CHANGE,
            operator=actor_name,
            operator_name=actor_name,
            to_status=new_status_val,
            detail={
                "from": old_status_val,
                "to": new_status_val,
                "synced_from": source_task_id,
                "sync_type": "duplicate",
            },
            description=f"{actor_name} 将工单状态变更为「{STATUS_LABEL.get(new_status_val, new_status_val)}」（重复工单同步）",
        )

        # 加系统评论（让被同步工单的讨论区也留痕）
        await _add_system_comment(
            db, tid,
            f"状态变更为「{STATUS_LABEL.get(new_status_val, new_status_val)}」——来自工单 #{source_task_id} 的重复同步",
            actor_name, token,
        )

    if synced_count > 0:
        await db.commit()

        # 广播 WS
        for tid in list(targets.keys()):
            try:
                await ws_broadcast_task_updated(tid)
            except Exception:
                pass

    return synced_count


async def _has_cycle(db: AsyncSession, source_id: int, target_id: int) -> bool:
    """DFS 检测 predecessor 关系是否成环：若从 target_id 出发沿 predecessor 边能回到 source_id，则成环。"""
    adj: Dict[int, List[int]] = {}
    all_result = await db.execute(
        select(TaskRelation.source_task_id, TaskRelation.target_task_id).where(
            TaskRelation.relation_type == RelationType.PREDECESSOR
        )
    )
    for s, t in all_result.all():
        adj.setdefault(s, []).append(t)

    # DFS 从 target_id 出发，看能否到达 source_id
    visited = set()
    stack = [target_id]
    while stack:
        node = stack.pop()
        if node == source_id:
            return True
        if node in visited:
            continue
        visited.add(node)
        stack.extend(adj.get(node, []))
    return False


# ── 关系树查询 ──

class RelationTreeNode(BaseModel):
    """关系树节点（一个工单的概要）"""
    id: int
    title: str
    status: str
    created_by_name: Optional[str] = None
    assigned_to_name: Optional[str] = None


class RelationTreeEdge(BaseModel):
    """关系树边（两个节点间的关系）"""
    source: int
    target: int
    relation_type: RelationType


class RelationTreeResponse(BaseModel):
    """关系树响应

    root_id:    渲染根节点（subtask 树最顶层的父工单）
    current_id: 用户实际打开的工单（用于前端高亮「当前」标签）
    nodes:      所有可达节点
    edges:      所有可达边
    """
    root_id: int
    current_id: int
    nodes: List[RelationTreeNode]
    edges: List[RelationTreeEdge]


async def _collect_relation_tree(
    db: AsyncSession,
    root_id: int,
    max_depth: int = 8,
) -> RelationTreeResponse:
    """从 root_id 出发，沿 subtask（向下）、predecessor（向上）、duplicate（平级）
    三个方向 BFS 遍历，收集所有可达节点和边。

    防环：visited 集合避免重复；subtask 不会成环（一个子任务只挂一个父）；
    predecessor 已有建关系时的成环校验，这里 BFS 也会兜底。
    """
    # 一次查出全表关系（数据量小，O(N) 可接受）
    result = await db.execute(select(TaskRelation))
    all_rels = result.scalars().all()

    # 构建邻接表：按关系类型分组
    # subtask: source(父) → [target(子)]，同时反向建 parent 映射
    # predecessor: source → [target(前置)]
    # duplicate: source ↔ target
    subtask_children: Dict[int, List[int]] = {}
    subtask_parent: Dict[int, int] = {}          # key=子, value=父
    predecessor_of: Dict[int, List[int]] = {}   # key=工单, value=它的前置工单IDs
    predecessor_dependents: Dict[int, List[int]] = {}  # key=前置工单, value=依赖它的工单IDs（反向）
    duplicate_with: Dict[int, set] = {}           # key=工单, value=重复工单IDs
    all_task_ids: set = {root_id}
    for rel in all_rels:
        if rel.relation_type == RelationType.SUBTASK:
            subtask_children.setdefault(rel.source_task_id, []).append(rel.target_task_id)
            subtask_parent[rel.target_task_id] = rel.source_task_id
            all_task_ids.add(rel.source_task_id)
            all_task_ids.add(rel.target_task_id)
        elif rel.relation_type == RelationType.PREDECESSOR:
            predecessor_of.setdefault(rel.source_task_id, []).append(rel.target_task_id)
            predecessor_dependents.setdefault(rel.target_task_id, []).append(rel.source_task_id)
            all_task_ids.add(rel.source_task_id)
            all_task_ids.add(rel.target_task_id)
        elif rel.relation_type == RelationType.DUPLICATE:
            duplicate_with.setdefault(rel.source_task_id, set()).add(rel.target_task_id)
            duplicate_with.setdefault(rel.target_task_id, set()).add(rel.source_task_id)
            all_task_ids.add(rel.source_task_id)
            all_task_ids.add(rel.target_task_id)

    # 向上追溯真正的 subtask 根节点（确保渲染从最顶层父工单开始）
    render_root_id = root_id
    _seen: set = set()
    while render_root_id in subtask_parent and render_root_id not in _seen:
        _seen.add(render_root_id)
        render_root_id = subtask_parent[render_root_id]

    # BFS：从 render_root_id（真正的 subtask 根）出发，沿三个方向遍历
    visited: set = set()
    queue: List[tuple] = [(render_root_id, 0)]
    visited.add(render_root_id)
    reachable: set = {render_root_id, root_id}  # root_id（当前工单）也必须在内

    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        # subtask：向下遍历子节点
        for child_id in subtask_children.get(current, []):
            reachable.add(child_id)
            if child_id not in visited:
                visited.add(child_id)
                queue.append((child_id, depth + 1))
        # subtask 反向：向上遍历父节点（当 BFS 从非根节点进入子树时补全祖先）
        if current in subtask_parent:
            parent_id = subtask_parent[current]
            reachable.add(parent_id)
            if parent_id not in visited:
                visited.add(parent_id)
                queue.append((parent_id, depth + 1))
        for pred_id in predecessor_of.get(current, []):
            reachable.add(pred_id)
            if pred_id not in visited:
                visited.add(pred_id)
                queue.append((pred_id, depth + 1))
        # 反向：谁依赖 current 作为前置（dependents）
        for dep_id in predecessor_dependents.get(current, []):
            reachable.add(dep_id)
            if dep_id not in visited:
                visited.add(dep_id)
                queue.append((dep_id, depth + 1))
        for dup_id in duplicate_with.get(current, set()):
            reachable.add(dup_id)
            if dup_id not in visited:
                visited.add(dup_id)
                queue.append((dup_id, depth + 1))

    # BFS 结束后，在 reachable 内部重新确定真正的 subtask 根节点
    # （之前的 render_root_id 只从当前工单向上追溯，可能没追溯到真正的根）
    has_subtask = any(
        t in subtask_children or t in subtask_parent
        for t in reachable
    )
    if has_subtask:
        # reachable 中有 subtask 关系：找"不在 subtask_parent 里，但有 subtask 子节点"的节点
        subtask_roots = [
            t for t in reachable
            if t not in subtask_parent and subtask_children.get(t)
        ]
        if subtask_roots:
            render_root_id = min(subtask_roots)  # 多个根时选 ID 最小的（稳定）

    if not reachable:
        reachable = {root_id}
    task_result = await db.execute(
        select(Task).where(Task.id.in_(reachable))
    )
    task_map: Dict[int, Task] = {t.id: t for t in task_result.scalars().all()}

    # 构建 nodes
    nodes: List[RelationTreeNode] = []
    for tid in sorted(reachable):
        t = task_map.get(tid)
        if not t:
            continue
        status_val = t.status.value if hasattr(t.status, 'value') else str(t.status)
        nodes.append(RelationTreeNode(
            id=t.id,
            title=t.title,
            status=status_val,
            created_by_name=t.created_by_name if hasattr(t, 'created_by_name') else None,
            assigned_to_name=t.assigned_to_name if hasattr(t, 'assigned_to_name') else None,
        ))

    # 构建 edges（只保留两端都在 reachable 内的边）
    edges: List[RelationTreeEdge] = []
    for rel in all_rels:
        if rel.source_task_id in reachable and rel.target_task_id in reachable:
            edges.append(RelationTreeEdge(
                source=rel.source_task_id,
                target=rel.target_task_id,
                relation_type=rel.relation_type,
            ))

    return RelationTreeResponse(root_id=render_root_id, current_id=root_id, nodes=nodes, edges=edges)


@router.get("/{task_id}/relations/tree", response_model=RelationTreeResponse)
async def get_task_relation_tree(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    max_depth: int = Query(8, ge=1, le=20, description="BFS 遍历深度上限"),
):
    """获取工单的完整关系树（从当前工单出发，BFS 遍历 subtask/predecessor/duplicate 三个方向）"""
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    return await _collect_relation_tree(db, task_id, max_depth=max_depth)


@router.get("/{task_id}/relations", response_model=List[TaskRelationResponse])
async def get_task_relations(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """获取工单的所有关联（双向：当前工单作为 source 或 target 的关系）"""
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 查询双向关系
    result = await db.execute(
        select(TaskRelation, Task).outerjoin(
            Task, Task.id == TaskRelation.target_task_id
        ).where(
            (TaskRelation.source_task_id == task_id) | (TaskRelation.target_task_id == task_id)
        )
    )

    relations: List[TaskRelationResponse] = []
    for rel, target in result.unique().all():
        # 加载 source 侧工单（若当前工单是 target）
        source = None
        if rel.source_task_id != task_id:
            from sqlalchemy import select as _sel
            src_result = await db.execute(_sel(Task).where(Task.id == rel.source_task_id))
            source = src_result.scalar_one_or_none()

        # 加载 target 侧工单（若当前工单是 source）
        target_info = None
        if rel.target_task_id != task_id and target:
            target_info = target

        relations.append(TaskRelationResponse(
            id=rel.id,
            source_task_id=rel.source_task_id,
            target_task_id=rel.target_task_id,
            relation_type=rel.relation_type,
            created_by=rel.created_by,
            created_at=rel.created_at,
            target=TaskRelationBrief.model_validate(target_info) if target_info else None,
            source=TaskRelationBrief.model_validate(source) if source else None,
        ))

    return relations


@router.post("/{task_id}/relations", response_model=TaskRelationResponse)
async def create_task_relation(
    task_id: int,
    body: TaskRelationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """创建工单关联"""
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 权限：复用 backend:tasks:operate
    if not has_permission_code(current_user, 'backend:tasks:operate'):
        raise HTTPException(status_code=403, detail="无权限创建工单关联")

    # 校验目标工单存在
    target_ticket = await TicketService.get_ticket_by_id(db, body.target_task_id)
    if not target_ticket:
        raise HTTPException(status_code=404, detail="目标工单不存在")

    # 禁止自引用
    if task_id == body.target_task_id:
        raise HTTPException(status_code=400, detail="不能关联自己")

    # 重复检查
    existing = await db.execute(
        select(TaskRelation).where(
            TaskRelation.source_task_id == task_id,
            TaskRelation.target_task_id == body.target_task_id,
            TaskRelation.relation_type == body.relation_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该关联已存在")

    # subtask 唯一性：一个子任务只能挂一个父工单
    if body.relation_type == RelationType.SUBTASK:
        dup_sub = await db.execute(
            select(TaskRelation).where(
                TaskRelation.target_task_id == body.target_task_id,
                TaskRelation.relation_type == RelationType.SUBTASK,
            )
        )
        if dup_sub.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="目标工单已是子任务，只能挂一个父工单")

    # predecessor 成环检测
    if body.relation_type == RelationType.PREDECESSOR:
        if await _has_cycle(db, task_id, body.target_task_id):
            raise HTTPException(status_code=400, detail="该关联会形成前置工单循环，不允许")

    # 冲突校验：同一对工单不能同时有 predecessor 和 subtask
    # 检查 target 是否已经是 source 的子任务
    conflict = await db.execute(
        select(TaskRelation).where(
            TaskRelation.source_task_id == task_id,
            TaskRelation.target_task_id == body.target_task_id,
            TaskRelation.relation_type.in_([RelationType.PREDECESSOR, RelationType.SUBTASK]),
        )
    )
    if conflict.scalar_one_or_none():
        existing_type = conflict.scalar_one().relation_type
        raise HTTPException(
            status_code=400,
            detail=f"两个工单已存在 {existing_type.value} 关系，不能同时建立前置/子任务关系",
        )

    username = actor_username(current_user)
    new_rel = TaskRelation(
        source_task_id=task_id,
        target_task_id=body.target_task_id,
        relation_type=body.relation_type,
        created_by=username,
    )
    db.add(new_rel)
    await db.commit()
    await db.refresh(new_rel)

    # 组装返回（带 target/source 概要）
    rel = new_rel
    result = await db.execute(
        select(TaskRelation).where(TaskRelation.id == rel.id)
    )
    refetched = result.scalar_one()
    target = await db.execute(select(Task).where(Task.id == refetched.target_task_id))
    target_t = target.scalar_one_or_none()
    source_t = None
    if refetched.source_task_id != task_id:
        src = await db.execute(select(Task).where(Task.id == refetched.source_task_id))
        source_t = src.scalar_one_or_none()

    return TaskRelationResponse(
        id=refetched.id,
        source_task_id=refetched.source_task_id,
        target_task_id=refetched.target_task_id,
        relation_type=refetched.relation_type,
        created_by=refetched.created_by,
        created_at=refetched.created_at,
        target=TaskRelationBrief.model_validate(target_t) if target_t else None,
        source=TaskRelationBrief.model_validate(source_t) if source_t else None,
    )


@router.delete("/{task_id}/relations/{relation_id}", response_model=dict)
async def delete_task_relation(
    task_id: int,
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """删除工单关联"""
    ticket = await TicketService.get_ticket_by_id(db, task_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 权限：复用 backend:tasks:operate
    if not has_permission_code(current_user, 'backend:tasks:operate'):
        raise HTTPException(status_code=403, detail="无权限删除工单关联")

    result = await db.execute(
        select(TaskRelation).where(
            TaskRelation.id == relation_id,
            (TaskRelation.source_task_id == task_id) | (TaskRelation.target_task_id == task_id),
        )
    )
    rel = result.scalar_one_or_none()
    if not rel:
        raise HTTPException(status_code=404, detail="关联不存在")

    await db.delete(rel)
    await db.commit()
    return {"message": "删除成功"}

