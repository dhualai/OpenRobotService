import logging
import threading
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.attributes import set_committed_value

from app.modules.tasks.models.ticket import Ticket, TicketComment, TicketStatus, TicketPriority, TicketType
from app.models.identity import UserDB
from app.modules.tasks.schemas.ticket import TicketCreate, TicketUpdate, TicketCommentCreate, TicketCommentUpdate, TicketQueryParams, TicketFilterRequest, QuotedComment
from app.core.config import settings
from app.utils.notification_utils import NotificationUtils
from app.utils.image_processor import ImageProcessor
from app.services.user_service import user_service
from app.core.user_identity import identity_keys, to_user_id


def convert_to_shanghai_time(dt: Optional[datetime]) -> Optional[datetime]:
    """deadline_at 写入前的时区归一。

    DB 已强制会话 UTC（db.py 的 _ensure_utc_session），naive DateTime 列统一存 UTC。
    前端 dayjs(...).toISOString() 传入的是 UTC aware datetime，这里剥时区转 naive UTC 即可，
    不再转 +8（否则前端 parseUtcDate 补 Z 会双重 +8）。
    """
    if dt is None:
        return None

    if dt.tzinfo is not None:
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.replace(tzinfo=None)

    return dt


def is_valid_id(id_value):
    return isinstance(id_value, int) and id_value > 0


def _cleanup_task_log_cache(ticket_id) -> None:
    """工单已解决/已关闭时，后台线程同步调用 AI 服务清理该工单的日志附件缓存。

    逻辑上只删 AI 侧缓存的日志文件 + 内存索引，不影响工单主流程；失败仅记日志。
    """
    try:
        import httpx
    except Exception:
        return
    try:
        url = f"{settings.AI_SERVICE_URL.rstrip('/')}/api/ai/task/log-cache/cleanup"
        with httpx.Client(timeout=10.0) as client:
            client.post(url, json={"task_id": str(ticket_id)})
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"清理工单日志缓存失败 ticket_id={ticket_id}: {e}"
        )


def spawn_log_cache_cleanup(ticket_id) -> None:
    """为已解决/关闭的工单派发后台日志缓存清理线程（best-effort，不阻塞主流程）。"""
    try:
        t = threading.Thread(
            target=_cleanup_task_log_cache, args=(ticket_id,), daemon=True
        )
        t.start()
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"派发日志缓存清理线程失败 ticket_id={ticket_id}: {e}"
        )


class TicketService:
    @classmethod
    async def _get_user_map(cls, token: Optional[str] = None) -> Dict[str, str]:
        return user_service.get_user_map()

    @classmethod
    async def _get_user_ids_by_name(cls, name: str, token: Optional[str] = None) -> List[str]:
        user_map = await cls._get_user_map(token)
        matched_ids = []
        
        for user_id, user_name in user_map.items():
            if isinstance(user_name, str) and name.lower() in user_name.lower():
                matched_ids.append(user_id)
        for key in cls._assignee_match_values(name):
            if key not in matched_ids:
                matched_ids.append(key)
        return matched_ids

    @staticmethod
    def _assignee_match_values(raw: str) -> List[str]:
        """assigned_to / created_by 存 users.id，筛选值可能仍是 username，两边都认。"""
        return identity_keys(raw)

    @staticmethod
    def _redispatch_tip(log, user_map: Dict[str, str]) -> Optional[str]:
        """按需求方案 §3.6 四分支规则生成派单结果提醒的一句话摘要（无提醒返回 None）。

        分支优先级：②未派到指定人 > ④拼音近似名 > ③同名；①画像不完整可叠加追加。
        """
        if log is None:
            return None
        assigned_name = user_map.get(log.assigned_id, log.assigned_id)
        preferred_id = log.preferred_id
        preferred_name = user_map.get(preferred_id, preferred_id) if preferred_id else None

        # ② 未派到指定人（简洁而礼貌的措辞，照顾用户情绪）
        if preferred_id and preferred_id != log.assigned_id:
            tip = f"很抱歉，您指定的【{preferred_name}】暂未采纳，已改派更合适的【{assigned_name}】处理"
        # ④ 拼音/近似名命中
        elif log.pinyin_match:
            tip = f"按拼音匹配到【{assigned_name}】（与输入【{preferred_name or assigned_name}】不同字），如非此人请更正"
        # ③ 同名命中
        elif log.name_collision:
            tip = f"指派人存在同名，已按评估选择【{assigned_name}】"
        else:
            tip = None

        # ① 画像不完整（可叠加追加）
        missing = ((log.profile or {}).get("missing") or []) if isinstance(log.profile, dict) else []
        if missing:
            suffix = "；该接单人画像不完整，待补充"
            tip = (tip + suffix) if tip else "该接单人画像不完整，待补充"
        return tip

    @staticmethod
    async def _redispatch_tips_map(
        db: AsyncSession, ids: List[int], user_map: Dict[str, str],
    ) -> Dict[int, Optional[str]]:
        """批量取各工单最新一条派单日志 → redispatch_tip（避免 N+1 查询）。

        单条 SQL：按 task_id + dispatch_round 排序，每组首行即最新一轮。
        """
        from sqlalchemy import select as _sel
        from app.models.task_dispatch_log import TaskDispatchLog
        if not ids:
            return {}
        rows = (await db.execute(
            _sel(TaskDispatchLog)
            .where(TaskDispatchLog.task_id.in_(ids))
            .order_by(TaskDispatchLog.task_id.asc(), TaskDispatchLog.dispatch_round.desc())
        )).scalars().all()
        seen: set = set()
        tips: Dict[int, Optional[str]] = {}
        for r in rows:
            if r.task_id in seen:
                continue
            seen.add(r.task_id)
            tips[r.task_id] = TicketService._redispatch_tip(r, user_map)
        return tips

    @staticmethod
    async def create_ticket(db: AsyncSession, ticket_data: TicketCreate, created_by: str, comment_attachment_map: dict, token: Optional[str] = None) -> Ticket:
        processed_attachments = []
        for attachment in ticket_data.attachments or []:
            # dict 附件（{object_path, filename} 结构，如远程截图）已是最终结构，直接落库；
            # 字符串才可能是 temp_id（需展开）或已就绪的 object_path（直接落库）。
            if isinstance(attachment, dict):
                processed_attachments.append(attachment)
                continue
            if attachment in comment_attachment_map:
                processed_attachments.extend(comment_attachment_map[attachment])
                comment_attachment_map[attachment].clear()
            else:
                processed_attachments.append(attachment)
        
        processed_description, _ = ImageProcessor.process_content_for_storage(
            ticket_data.description,
            0,
            0
        )

        created_by_id = to_user_id(created_by) or created_by
        assigned_to_raw = ticket_data.assigned_to
        assigned_to_id = (to_user_id(assigned_to_raw) or assigned_to_raw) if assigned_to_raw else created_by_id

        user_map = await TicketService._get_user_map(token)
        created_by_name = user_map.get(created_by_id, created_by)

        async with db.begin():
            db_ticket = Ticket(
                title=ticket_data.title,
                description=processed_description,
                ticket_type=ticket_data.ticket_type,
                priority=ticket_data.priority,
                related_resource_id=ticket_data.related_resource_id,
                created_by=created_by_id,
                tags=ticket_data.tags,
                metadata_info=ticket_data.metadata_info,
                project_name=ticket_data.project_name,
                project_id=ticket_data.project_id,
                deadline_at=convert_to_shanghai_time(ticket_data.deadline_at),
                # 接单人：尊重前端传入的 assigned_to（兜底双工单场景下工单2 直接指定项目负责人）；
                # 未传时回退为创建人（原有行为）。传了 assigned_to 说明已明确派单，状态置为 IN_PROGRESS，
                # 否则工单会留在 NEW 被派单 Worker 再次派单。
                assigned_to=assigned_to_id,
                customer=ticket_data.customer,
                attachments=processed_attachments,
                status=TicketStatus.IN_PROGRESS if assigned_to_raw else TicketStatus.NEW
            )
            db.add(db_ticket)
            await db.flush()

            ticket_id = db_ticket.id

        result = await db.execute(
            select(Ticket)
            .where(Ticket.id == db_ticket.id)
            .options(joinedload(Ticket.comments))
        )

        ticket = result.unique().scalar_one()
        setattr(ticket, "created_by_name", created_by_name)
        setattr(ticket, "reporter_name", created_by_name)
        setattr(ticket, "assigned_to_name", created_by_name)
        setattr(ticket, "assignee_name", created_by_name)

        # 新建工单通知：显式指派受理人时（双工单工单2 / 直接指派），通知受理人
        import logging
        logger = logging.getLogger(__name__)
        if ticket_data.assigned_to:
            logger.info(f"准备发送新建工单通知: ticket_id={ticket.id}, assignee={ticket_data.assigned_to}, operator={created_by_name}")
            try:
                await NotificationUtils.send_ticket_create_notification(
                    ticket_id=ticket.id,
                    title=ticket.title or "",
                    project_name=ticket.project_name or "",
                    operator=created_by_name,
                    deadline_at=ticket.deadline_at,
                    user_names=[assigned_to_id or ticket_data.assigned_to],
                    token=token,
                )
                logger.info(f"新建工单通知已发送: ticket_id={ticket.id}, assignee={ticket_data.assigned_to}")
            except Exception as e:
                logger.warning(f"新建工单通知发送失败 ticket_id={ticket.id}: {e}")
        else:
            logger.info(f"工单未显式指派受理人，跳过新建通知: ticket_id={ticket.id}, assigned_to={ticket.assigned_to}")

        return ticket

    @staticmethod
    def _apply_string_op(query, column, value: str, op: Optional[str], default_op: str = 'equals'):
        effective_op = op or default_op
        if effective_op == 'contains':
            return query.where(column.ilike(f'%{value}%'))
        elif effective_op == 'notEquals':
            return query.where(column != value)
        else:
            return query.where(column == value)

    @staticmethod
    def _apply_int_op(query, column, value: int, op: Optional[str]):
        if op == 'gt':
            return query.where(column > value)
        elif op == 'gte':
            return query.where(column >= value)
        elif op == 'lt':
            return query.where(column < value)
        elif op == 'lte':
            return query.where(column <= value)
        elif op == 'ne':
            return query.where(column != value)
        else:
            return query.where(column == value)

    @staticmethod
    async def get_tickets(db: AsyncSession, query_params: TicketQueryParams, token: Optional[str] = None) -> Dict[str, Any]:
        query = select(Ticket).where(Ticket.id.isnot(None))

        if query_params.id is not None:
            query = TicketService._apply_int_op(
                query, Ticket.id, query_params.id, query_params.id_op)

        if query_params.title:
            query = TicketService._apply_string_op(
                query, Ticket.title, query_params.title, query_params.title_op, 'contains')

        if query_params.status:
            status_values = [s.strip() for s in query_params.status.split(',')]
            status_enums = []
            for status_str in status_values:
                try:
                    status_enum = TicketStatus(status_str)
                    status_enums.append(status_enum)
                except ValueError:
                    continue

            if status_enums:
                query = query.where(Ticket.status.in_(status_enums))

        if query_params.priority is not None:
            query = query.where(Ticket.priority == query_params.priority)

        if query_params.ticket_type is not None:
            query = query.where(Ticket.ticket_type == query_params.ticket_type)

        if query_params.created_by:
            keys = TicketService._assignee_match_values(query_params.created_by)
            op = query_params.created_by_op or 'equals'
            if op == 'contains' and keys:
                query = query.where(or_(*[Ticket.created_by.ilike(f"%{k}%") for k in keys]))
            elif op == 'notEquals' and keys:
                query = query.where(~Ticket.created_by.in_(keys))
            elif keys:
                query = query.where(Ticket.created_by.in_(keys))

        if query_params.created_by_name:
            matched_ids = await TicketService._get_user_ids_by_name(query_params.created_by_name, token)
            if matched_ids:
                query = query.where(Ticket.created_by.in_(matched_ids))

        if query_params.assigned_to:
            keys = TicketService._assignee_match_values(query_params.assigned_to)
            op = query_params.assigned_to_op or 'equals'
            if op == 'contains' and keys:
                query = query.where(or_(*[Ticket.assigned_to.ilike(f"%{k}%") for k in keys]))
            elif op == 'notEquals' and keys:
                query = query.where(~Ticket.assigned_to.in_(keys))
            elif keys:
                query = query.where(Ticket.assigned_to.in_(keys))

        if query_params.assigned_to_name:
            matched_ids = await TicketService._get_user_ids_by_name(query_params.assigned_to_name, token)
            if matched_ids:
                query = query.where(Ticket.assigned_to.in_(matched_ids))

        if query_params.customer:
            keys = TicketService._assignee_match_values(query_params.customer)
            op = query_params.customer_op or 'equals'
            if op == 'contains' and keys:
                query = query.where(or_(*[Ticket.customer.ilike(f"%{k}%") for k in keys]))
            elif op == 'notEquals' and keys:
                query = query.where(~Ticket.customer.in_(keys))
            elif keys:
                query = query.where(Ticket.customer.in_(keys))

        if query_params.customer_name:
            matched_ids = await TicketService._get_user_ids_by_name(query_params.customer_name, token)
            if matched_ids:
                query = query.where(Ticket.customer.in_(matched_ids))

        if query_params.related_resource_id:
            query = TicketService._apply_int_op(
                query, Ticket.related_resource_id, query_params.related_resource_id, query_params.related_resource_id_op)

        if query_params.project_name:
            query = TicketService._apply_string_op(
                query, Ticket.project_name, query_params.project_name, query_params.project_name_op, 'contains')

        if query_params.project_id:
            query = TicketService._apply_string_op(
                query, Ticket.project_id, query_params.project_id, query_params.project_id_op, 'equals')

        if query_params.source:
            query = TicketService._apply_string_op(
                query, Ticket.source, query_params.source, query_params.source_op, 'equals')

        if query_params.deadline_at:
            query = query.where(Ticket.deadline_at == query_params.deadline_at)

        if query_params.created_at_start:
            query = query.where(Ticket.created_at >= query_params.created_at_start)
        if query_params.created_at_end:
            query = query.where(Ticket.created_at <= query_params.created_at_end)

        if query_params.updated_at_start:
            query = query.where(Ticket.updated_at >= query_params.updated_at_start)
        if query_params.updated_at_end:
            query = query.where(Ticket.updated_at <= query_params.updated_at_end)

        if query_params.resolved_at_start:
            query = query.where(Ticket.resolved_at >= query_params.resolved_at_start)
        if query_params.resolved_at_end:
            query = query.where(Ticket.resolved_at <= query_params.resolved_at_end)

        if query_params.closed_at_start:
            query = query.where(Ticket.closed_at >= query_params.closed_at_start)
        if query_params.closed_at_end:
            query = query.where(Ticket.closed_at <= query_params.closed_at_end)

        if query_params.deadline_at_start:
            query = query.where(Ticket.deadline_at >= query_params.deadline_at_start)
        if query_params.deadline_at_end:
            query = query.where(Ticket.deadline_at <= query_params.deadline_at_end)

        if query_params.keyword:
            keyword = f"%{query_params.keyword}%"
            query = query.where(
                or_(
                    Ticket.title.ilike(keyword),
                    Ticket.description.ilike(keyword)
                )
            )

        if query_params.tag:
            query = query.where(Ticket.tags.contains([query_params.tag]))

        count_query = select(func.count(Ticket.id)).select_from(Ticket)
        if query.whereclause is not None:
            count_query = count_query.where(query.whereclause)

        total_result = await db.execute(count_query)
        total = total_result.scalar_one()

        page = query_params.page
        size = query_params.size
        skip = (page - 1) * size

        query = query.order_by(Ticket.created_at.desc()).offset(skip).limit(size)

        result = await db.execute(query)
        tickets = result.scalars().all()

        user_map = await TicketService._get_user_map(token)
        
        for ticket in tickets:
            setattr(ticket, "created_by_name", user_map.get(ticket.created_by, ticket.created_by))
            setattr(ticket, "reporter_name", user_map.get(ticket.created_by, ticket.created_by))
            if ticket.assigned_to:
                setattr(ticket, "assigned_to_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
                setattr(ticket, "assignee_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
            if ticket.customer:
                setattr(ticket, "customer_name", user_map.get(ticket.customer, ticket.customer))

        # 二次派单感知增强（M3）：批量生成派单结果提醒 redispatch_tip（避免 N+1）
        tip_map = await TicketService._redispatch_tips_map(db, [t.id for t in tickets], user_map)
        for ticket in tickets:
            setattr(ticket, "redispatch_tip", tip_map.get(ticket.id))

        pages = (total + size - 1) // size

        return {
            "items": tickets,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages
        }

    @staticmethod
    async def _build_single_filter(query, f, FIELD_MAPPING, NUMBER_OPS, TEXT_OPS, ENUM_OPS, DATETIME_OPS, token):
        field = f.field
        op = f.op
        value = f.value

        if field not in FIELD_MAPPING:
            return query

        column, field_type = FIELD_MAPPING[field]

        if op == 'is_null':
            return query.where(column.is_(None))
        elif op == 'not_null':
            return query.where(column.isnot(None))

        if field_type == 'name':
            matched_ids = await TicketService._get_user_ids_by_name(value, token)
            if matched_ids:
                return query.where(column.in_(matched_ids))
            else:
                return query.where(column.in_(['__no_match__']))

        if field_type == 'number':
            if op not in NUMBER_OPS:
                op = 'eq'
            if op == 'gt':
                return query.where(column > value)
            elif op == 'lt':
                return query.where(column < value)
            elif op == 'ge':
                return query.where(column >= value)
            elif op == 'le':
                return query.where(column <= value)
            elif op == 'eq':
                return query.where(column == value)
            elif op == 'ne':
                return query.where(column != value)

        elif field_type == 'text':
            if op not in TEXT_OPS:
                op = 'contains'
            assignee_keys = (
                TicketService._assignee_match_values(str(value))
                if field in ('assignedTo', 'createdBy', 'customer') and isinstance(value, str)
                else None
            )
            if op == 'contains':
                if assignee_keys:
                    return query.where(or_(*[column.ilike(f"%{k}%") for k in assignee_keys]))
                return query.where(column.ilike(f"%{value}%"))
            elif op == 'not_contains':
                if assignee_keys:
                    return query.where(~or_(*[column.ilike(f"%{k}%") for k in assignee_keys]))
                return query.where(~column.ilike(f"%{value}%"))
            elif op == 'eq':
                if assignee_keys:
                    return query.where(column.in_(assignee_keys))
                return query.where(column == value)
            elif op == 'ne':
                if assignee_keys:
                    return query.where(~column.in_(assignee_keys))
                return query.where(column != value)

        elif field_type == 'enum':
            if op not in ENUM_OPS:
                op = 'eq'
            if op == 'eq':
                return query.where(column == value)
            elif op == 'ne':
                return query.where(column != value)
            elif op == 'in':
                return query.where(column.in_(value))
            elif op == 'not_in':
                return query.where(~column.in_(value))

        elif field_type == 'datetime':
            if op not in DATETIME_OPS:
                op = 'eq'
            if op == 'gt':
                return query.where(column > value)
            elif op == 'lt':
                return query.where(column < value)
            elif op == 'ge':
                return query.where(column >= value)
            elif op == 'le':
                return query.where(column <= value)
            elif op == 'eq':
                return query.where(column == value)
            elif op == 'ne':
                return query.where(column != value)

        return query

    @staticmethod
    async def _apply_nested_filter(query, filter_item, FIELD_MAPPING, NUMBER_OPS, TEXT_OPS, ENUM_OPS, DATETIME_OPS, token):
        if filter_item.or_conditions:
            or_conditions = []
            for or_item in filter_item.or_conditions:
                if or_item.and_conditions:
                    and_query = select(Ticket).where(Ticket.id.isnot(None))
                    for and_item in or_item.and_conditions:
                        and_query = await TicketService._apply_nested_filter(and_query, and_item, FIELD_MAPPING, NUMBER_OPS, TEXT_OPS, ENUM_OPS, DATETIME_OPS, token)
                    or_conditions.append(and_query.whereclause)
                elif or_item.field:
                    simple_query = select(Ticket).where(Ticket.id.isnot(None))
                    simple_query = await TicketService._build_single_filter(simple_query, or_item, FIELD_MAPPING, NUMBER_OPS, TEXT_OPS, ENUM_OPS, DATETIME_OPS, token)
                    if simple_query.whereclause is not None:
                        or_conditions.append(simple_query.whereclause)
            if or_conditions:
                query = query.where(or_(*or_conditions))
            return query

        if filter_item.and_conditions:
            for and_item in filter_item.and_conditions:
                query = await TicketService._apply_nested_filter(query, and_item, FIELD_MAPPING, NUMBER_OPS, TEXT_OPS, ENUM_OPS, DATETIME_OPS, token)
            return query

        if filter_item.field:
            return await TicketService._build_single_filter(query, filter_item, FIELD_MAPPING, NUMBER_OPS, TEXT_OPS, ENUM_OPS, DATETIME_OPS, token)

        return query

    @staticmethod
    async def filter_tickets(db: AsyncSession, filter_request: TicketFilterRequest, token: Optional[str] = None) -> Dict[str, Any]:
        query = select(Ticket).where(Ticket.id.isnot(None))

        FIELD_MAPPING = {
            'id': (Ticket.id, 'number'),
            'title': (Ticket.title, 'text'),
            'status': (Ticket.status, 'enum'),
            'priority': (Ticket.priority, 'enum'),
            # 注意：必须用真实列 task_type（ticket_type 是模型上的 property，不能参与 SQL 表达式）
            'ticketType': (Ticket.task_type, 'enum'),
            'createdBy': (Ticket.created_by, 'text'),
            'createdByName': (Ticket.created_by, 'name'),
            'assignedTo': (Ticket.assigned_to, 'text'),
            'assignedToName': (Ticket.assigned_to, 'name'),
            'customer': (Ticket.customer, 'text'),
            'customerName': (Ticket.customer, 'name'),
            'relatedResourceId': (Ticket.related_resource_id, 'number'),
            'projectName': (Ticket.project_name, 'text'),
            'projectId': (Ticket.project_id, 'text'),
            'source': (Ticket.source, 'text'),
            'createdAt': (Ticket.created_at, 'datetime'),
            'updatedAt': (Ticket.updated_at, 'datetime'),
            'resolvedAt': (Ticket.resolved_at, 'datetime'),
            'closedAt': (Ticket.closed_at, 'datetime'),
            'deadlineAt': (Ticket.deadline_at, 'datetime'),
            # 回合协商：支持按"最近改 step 的操作方侧标识"过滤（assigned/creator）
            'stepUpdatedBy': (Ticket.step_last_updated_by, 'enum'),
            # 当前协商节点是否已协商一致：用于"待我处理"按回合精确过滤
            'currStepAgreed': (Ticket.curr_step_agreed, 'enum'),
        }

        NUMBER_OPS = {'gt', 'lt', 'ge', 'le', 'eq', 'ne', 'is_null', 'not_null'}
        TEXT_OPS = {'contains', 'not_contains', 'eq', 'ne', 'is_null', 'not_null'}
        ENUM_OPS = {'eq', 'ne', 'in', 'not_in', 'is_null', 'not_null'}
        DATETIME_OPS = {'gt', 'lt', 'ge', 'le', 'eq', 'ne', 'is_null', 'not_null'}

        for f in filter_request.filters:
            query = await TicketService._apply_nested_filter(query, f, FIELD_MAPPING, NUMBER_OPS, TEXT_OPS, ENUM_OPS, DATETIME_OPS, token)

        if filter_request.sorts:
            for sort in filter_request.sorts:
                field = sort.field
                direction = sort.direction.lower()
                
                if field not in FIELD_MAPPING:
                    continue
                
                column, _ = FIELD_MAPPING[field]
                
                if direction == 'asc':
                    query = query.order_by(column.asc())
                else:
                    query = query.order_by(column.desc())
        else:
            query = query.order_by(Ticket.created_at.desc())

        count_query = select(func.count(Ticket.id)).select_from(Ticket)
        if query.whereclause is not None:
            count_query = count_query.where(query.whereclause)

        total_result = await db.execute(count_query)
        total = total_result.scalar_one()

        page = filter_request.page
        size = filter_request.size
        skip = (page - 1) * size

        query = query.offset(skip).limit(size)

        result = await db.execute(query)
        tickets = result.scalars().all()

        user_map = await TicketService._get_user_map(token)
        
        for ticket in tickets:
            setattr(ticket, "created_by_name", user_map.get(ticket.created_by, ticket.created_by))
            setattr(ticket, "reporter_name", user_map.get(ticket.created_by, ticket.created_by))
            if ticket.assigned_to:
                setattr(ticket, "assigned_to_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
                setattr(ticket, "assignee_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
            if ticket.customer:
                setattr(ticket, "customer_name", user_map.get(ticket.customer, ticket.customer))

        pages = (total + size - 1) // size

        return {
            "items": tickets,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages
        }

    @staticmethod
    async def get_ticket_by_id(db: AsyncSession, ticket_id: int, load_comments: bool = False, token: Optional[str] = None) -> Optional[Ticket]:
        import logging
        logger = logging.getLogger(__name__)
        
        if not is_valid_id(ticket_id):
            return None
        
        query = select(Ticket).where(Ticket.id == ticket_id)
        
        result = await db.execute(query)
        ticket = result.scalar_one_or_none()
        
        if ticket and not load_comments:
            from sqlalchemy import update
            await db.execute(
                update(Ticket)
                .where(Ticket.id == ticket_id)
                .values(view_count=Ticket.view_count + 1)
            )
            await db.commit()
            result = await db.execute(query)
            ticket = result.scalar_one_or_none()
        
        if ticket:
            user_map = await TicketService._get_user_map(token)
            # user_map 为进程内缓存（10min TTL）；若某 id（新加入用户）解析不到名字会回退成裸 id，
            # 导致前端气泡显示 id 而非名字。检测到缺失时强制失效缓存重建一次，再解析真实名字。
            _need_refresh = (
                (ticket.assigned_to and not user_map.get(ticket.assigned_to))
                or (ticket.created_by and not user_map.get(ticket.created_by))
                or (ticket.customer and not user_map.get(ticket.customer))
            )
            if _need_refresh:
                user_service.invalidate_cache()
                user_map = await TicketService._get_user_map(token)
            setattr(ticket, "created_by_name", user_map.get(ticket.created_by, ticket.created_by))
            setattr(ticket, "reporter_name", user_map.get(ticket.created_by, ticket.created_by))
            if ticket.assigned_to:
                # 只接受解析出的真实姓名，解析不到则返回 None（不回落成裸 id）——
                # 前端据此继续轮询等待真实名字，而不是把 id 当名字展示。
                setattr(ticket, "assigned_to_name", user_map.get(ticket.assigned_to))
                setattr(ticket, "assignee_name", user_map.get(ticket.assigned_to))
            if ticket.customer:
                setattr(ticket, "customer_name", user_map.get(ticket.customer, ticket.customer))
        
        if ticket and load_comments:
            logger.info(f"开始加载评论: ticket_id={ticket_id}")
            try:
                # 手动查询评论列表，避免 async session 下关系懒加载触发 MissingGreenlet
                # （getattr(ticket, 'comments') 在关系未 eager 填充时会走同步 IO → greenlet 报错）
                comments_result = await db.execute(
                    select(TicketComment)
                    .where(TicketComment.task_id == ticket_id)
                    .order_by(TicketComment.created_at.desc())
                )
                comments = comments_result.scalars().all()

                user_map = await TicketService._get_user_map(token)
                for comment in comments:
                    # 复用 _attach_comment_meta：统一拼装 created_by_name / 图片处理 / 引用块 quoted
                    # （修复刷新后引用消息丢失：原仅设 created_by_name 未拼 quoted）
                    await TicketService._attach_comment_meta(db, comment, user_map)

                # 将评论列表固化为已提交值：response_model 序列化发生在 db session 关闭后，
                # 直接访问 ticket.comments 会触发懒加载 → async 下 MissingGreenlet。
                set_committed_value(ticket, 'comments', list(comments))
                logger.info(f"评论加载成功: ticket_id={ticket_id}, comment_count={len(comments)}")
            except Exception as e:
                logger.error(f"评论加载失败: ticket_id={ticket_id}, error={str(e)}", exc_info=True)
                raise
        elif ticket:
            # 未请求评论时也设空列表，避免序列化时触发 lazy load → MissingGreenlet
            set_committed_value(ticket, 'comments', [])
        return ticket

    @staticmethod
    async def update_ticket(db: AsyncSession, ticket_id: int, ticket_update: TicketUpdate, token: Optional[str] = None, operator_id: Optional[str] = None) -> Dict[str, Any]:
        ticket = await TicketService.get_ticket_by_id(db, ticket_id)
        if not ticket:
            return {"ticket": None, "notification": None}

        # operation_type 仅用于操作日志识别，不入库、不入通知（与 API 层及 schema 注释一致）
        update_data = ticket_update.dict(exclude_unset=True, exclude={'operation_type'})

        for field, value in update_data.items():
            if field == "deadline_at":
                value = convert_to_shanghai_time(value)
            if field == "assigned_to" and value:
                value = to_user_id(value) or value
            setattr(ticket, field, value)

        if "status" in update_data:
            if update_data["status"] == TicketStatus.RESOLVED and "resolved_at" not in update_data:
                ticket.resolved_at = func.now()
            elif update_data["status"] == TicketStatus.CLOSED and "closed_at" not in update_data:
                ticket.closed_at = func.now()

        ticket.updated_at = func.now()

        await db.commit()

        notification_result = None
        try:
            notify_users = []
            if ticket.created_by:
                notify_users.append(ticket.created_by)
            if ticket.assigned_to:
                notify_users.append(ticket.assigned_to)
            if ticket.customer:
                notify_users.append(ticket.customer)
            notify_users = list(set(notify_users))
            if operator_id:
                operator_keys = set(identity_keys(operator_id))
                notify_users = [u for u in notify_users if u not in operator_keys]
            if notify_users:
                user_map = await TicketService._get_user_map(token)
                
                # assigned_to 变更由 API 层 send_ticket_reassign_notification 专门处理，
                # 此处仅处理其他字段变更的通知，避免重复发送
                notify_update_data = {k: v for k, v in update_data.items() if k != 'assigned_to'}
                if notify_update_data:
                    update_details = []
                    for field, value in notify_update_data.items():
                        if isinstance(value, list):
                            value_str = ', '.join(str(item) for item in value)
                        else:
                            value_str = str(value)
                        
                        if field == 'customer':
                            value_str = user_map.get(value_str, value_str)
                        
                        update_details.append(f"{field}: {value_str}")
                    update_content = '\n'.join(update_details)
                    
                    operator_name = user_map.get(operator_id, operator_id)
                    notification_result = await NotificationUtils.send_ticket_update_notification(
                        ticket_id=ticket_id,
                        title=ticket.title,
                        operator=operator_name,
                        project_name=ticket.project_name,
                        update_content=update_content,
                        user_names=notify_users,
                        token=token
                    )
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send notification for ticket {ticket_id}: {str(e)}")
            notification_result = {
                "code": 500,
                "message": f"通知发送失败: {str(e)}",
                "data": {
                    "status": "failed"
                }
            }

        result = await db.execute(
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(joinedload(Ticket.comments))
        )
        updated_ticket = result.unique().scalar_one_or_none()
        
        return {
            "ticket": updated_ticket,
            "notification": notification_result
        }

    @staticmethod
    async def delete_ticket(db: AsyncSession, ticket_id: int, is_admin: bool = False) -> bool:
        ticket = await TicketService.get_ticket_by_id(db, ticket_id)
        if not ticket:
            return False
        
        if is_admin:
            from sqlalchemy import delete
            await db.execute(delete(TicketComment).where(TicketComment.task_id == ticket_id))
            await db.delete(ticket)
        else:
            ticket.status = TicketStatus.CLOSED
            ticket.closed_at = func.now()
        
        await db.commit()
        return True

    @staticmethod
    async def _attach_comment_meta(db: AsyncSession, comment: TicketComment, user_map: Dict[str, str]) -> TicketComment:
        """为评论附加展示用元数据：创建人姓名、头像、引用评论摘要、响应态内容。"""
        setattr(comment, "created_by_name", user_map.get(comment.created_by, comment.created_by))
        # 头像：created_by 可能是 users.id 也可能是 username，两者都查（离线作者也能取到头像，
        # 修复「气泡头像有时显示、有时文字缺省」——原先前端只依赖在线成员列表拿头像）
        try:
            avatar_res = await db.execute(
                select(UserDB.avatar_resource_id).where(
                    or_(UserDB.id == comment.created_by, UserDB.username == comment.created_by)
                ).limit(1)
            )
            avatar_rid = avatar_res.scalar_one_or_none()
            setattr(comment, "created_by_avatar_resource_id", avatar_rid)
        except Exception:
            setattr(comment, "created_by_avatar_resource_id", None)
        try:
            comment.content = ImageProcessor.process_content_for_response(comment.content)
        except Exception:
            pass
        reply_to = getattr(comment, "reply_to", None)
        if reply_to:
            try:
                res = await db.execute(select(TicketComment).where(TicketComment.id == reply_to))
                qc = res.scalar_one_or_none()
                if qc:
                    qname = user_map.get(qc.created_by, qc.created_by)
                    setattr(comment, "quoted", QuotedComment(
                        id=qc.id,
                        content=qc.content or "",
                        created_by_name=qname,
                    ))
            except Exception:
                pass
        return comment

    @staticmethod
    async def add_comment(db: AsyncSession, ticket_id: int, comment_data: TicketCommentCreate, created_by: str, comment_attachment_map: dict, token: Optional[str] = None) -> Optional[TicketComment]:
        ticket = await TicketService.get_ticket_by_id(db, ticket_id)
        if not ticket:
            return None

        processed_content, _ = ImageProcessor.process_content_for_storage(
            comment_data.content,
            ticket_id,
            0
        )
        processed_attachments = []
        for attachment in comment_data.attachments or []:
            if attachment in comment_attachment_map:
                processed_attachments.extend(comment_attachment_map[attachment])
                comment_attachment_map[attachment].clear()
            else:
                processed_attachments.append(attachment)

        comment = TicketComment(
            ticket_id=ticket_id,
            content=processed_content,
            is_public=comment_data.is_public,
            attachments=processed_attachments,
            created_by=created_by,
            reply_to=comment_data.reply_to
        )

        ticket.reply_count = ticket.reply_count + 1

        db.add(comment)
        await db.commit()
        await db.refresh(comment)
        await db.refresh(ticket)

        user_map = await TicketService._get_user_map(token)
        await TicketService._attach_comment_meta(db, comment, user_map)

        return comment

    @staticmethod
    async def get_comments(db: AsyncSession, ticket_id: int, token: Optional[str] = None) -> List[TicketComment]:
        import logging
        logger = logging.getLogger(__name__)

        result = await db.execute(
            select(TicketComment)
            .where(TicketComment.task_id == ticket_id)
            .order_by(TicketComment.created_at.desc())
            .execution_options(populate_existing=True)
        )
        comments = list(result.scalars().all())
        logger.info(f"查询到评论数: ticket_id={ticket_id}, count={len(comments)}")

        user_map = await TicketService._get_user_map(token)

        for comment in comments:
            await TicketService._attach_comment_meta(db, comment, user_map)

        return comments

    @staticmethod
    async def update_comment(db: AsyncSession, comment_id: int, comment_update: TicketCommentUpdate, comment_attachment_map: dict) -> Optional[TicketComment]:
        result = await db.execute(
            select(TicketComment).where(TicketComment.id == comment_id)
        )
        comment = result.scalar_one_or_none()
        
        if not comment:
            return None
        
        update_data = comment_update.dict(exclude_unset=True)
        
        if "content" in update_data:
            processed_content, _ = ImageProcessor.process_content_for_storage(
                update_data["content"],
                int(comment.ticket_id),
                comment_id
            )
            update_data["content"] = processed_content

        processed_attachments = []
        for attachment in update_data.get("attachments", []):
            if attachment in comment_attachment_map:
                processed_attachments.extend(comment_attachment_map[attachment])
                comment_attachment_map[attachment].clear()
            else:
                processed_attachments.append(attachment)

        comment.updated_at = func.now()
        comment.attachments = processed_attachments
        comment.content = update_data["content"]

        await db.commit()
        await db.refresh(comment)
        return comment

    @staticmethod
    async def delete_comment(db: AsyncSession, comment_id: int) -> bool:
        result = await db.execute(
            select(TicketComment).where(TicketComment.id == comment_id)
        )
        comment = result.scalar_one_or_none()
        
        if not comment:
            return False
        
        ticket = await TicketService.get_ticket_by_id(db, int(comment.ticket_id))
        if ticket and ticket.reply_count > 0:
            ticket.reply_count = ticket.reply_count - 1
        
        await db.delete(comment)
        await db.commit()
        return True

    @staticmethod
    async def update_ticket_status(db: AsyncSession, ticket_id: int, status: TicketStatus, token: Optional[str] = None, operator_id: Optional[str] = None, resolution_summary: Optional[str] = None) -> Optional[Ticket]:
        ticket = await TicketService.get_ticket_by_id(db, ticket_id)
        if not ticket:
            return None
        
        ticket.status = status
        
        if status == TicketStatus.RESOLVED:
            ticket.resolved_at = func.now()
        elif status == TicketStatus.CLOSED:
            ticket.closed_at = func.now()

        # 工单进入最终态（已解决/已关闭）→ 后台清理该工单的日志附件缓存（AI 侧）
        if status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            spawn_log_cache_cleanup(ticket_id)

        # 结束工单（resolved）时，若带解决方式，则写入 metadata_info.resolution_summary
        if status == TicketStatus.RESOLVED and resolution_summary is not None:
            meta = dict(ticket.metadata_info or {})
            meta["resolution_summary"] = resolution_summary
            from datetime import datetime
            meta["resolution_summary_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            meta["resolution_gen_state"] = "confirmed"
            ticket.metadata_info = meta
        
        ticket.updated_at = func.now()
        
        await db.commit()
        
        try:
            notify_users = []
            if ticket.created_by:
                notify_users.append(ticket.created_by)
            if ticket.assigned_to:
                notify_users.append(ticket.assigned_to)
            if ticket.customer:
                notify_users.append(ticket.customer)
            notify_users = list(set(notify_users))
            if operator_id:
                operator_keys = set(identity_keys(operator_id))
                notify_users = [u for u in notify_users if u not in operator_keys]
            
            if notify_users:
                user_map = await TicketService._get_user_map(token)
                operator_name = user_map.get(operator_id, operator_id or '系统')
                update_content = f"status: {status.value}"
                
                await NotificationUtils.send_ticket_update_notification(
                    ticket_id=ticket_id,
                    title=ticket.title,
                    operator=operator_name,
                    project_name=ticket.project_name,
                    update_content=update_content,
                    user_names=notify_users,
                    token=token
                )
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send notification for ticket {ticket_id}: {str(e)}")
        
        result = await db.execute(
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(joinedload(Ticket.comments))
        )
        return result.unique().scalar_one_or_none()

    @staticmethod
    async def assign_ticket(db: AsyncSession, ticket_id: int, user_id: str) -> Optional[Ticket]:
        ticket = await TicketService.get_ticket_by_id(db, ticket_id)
        if not ticket:
            return None

        # 派单只写 assigned_to，不改状态——工单保持「新建」，由处理人「首次响应」后才进入「处理中」
        ticket.assigned_to = to_user_id(user_id) or user_id

        await db.commit()
        result = await db.execute(
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(joinedload(Ticket.comments))
        )
        return result.unique().scalar_one_or_none()

    @staticmethod
    async def get_ticket_stats(db: AsyncSession) -> Dict[str, Any]:
        stats = {}

        for status in TicketStatus:
            result = await db.execute(
                select(func.count(Ticket.id)).where(Ticket.status == status)
            )
            count = result.scalar() or 0
            stats[status.value] = count

        total_result = await db.execute(select(func.count(Ticket.id)))
        total = total_result.scalar() or 0

        return {
            "total": total,
            "statistics": stats,
            "breakdown": {
                "opened": stats.get("new", 0) + stats.get("in_progress", 0) + stats.get("pending", 0),
                "closed": stats.get("closed", 0),
                "resolved": stats.get("resolved", 0),
                "in_progress": stats.get("in_progress", 0)
            }
        }

    @staticmethod
    async def get_filtered_tickets(db: AsyncSession, current_user_name: str, page: int = 1, size: int = 10, token: Optional[str] = None) -> Dict[str, Any]:
        identity_keys_me = TicketService._assignee_match_values(current_user_name)
        filter_condition = or_(
            and_(
                Ticket.status == TicketStatus.NEW,
                Ticket.created_by.in_(identity_keys_me) if identity_keys_me else Ticket.created_by == current_user_name
            ),
            and_(
                Ticket.status.in_([TicketStatus.IN_PROGRESS, TicketStatus.PENDING]),
                Ticket.assigned_to.in_(identity_keys_me) if identity_keys_me else Ticket.assigned_to == current_user_name
            ),
            and_(
                Ticket.status == TicketStatus.RESOLVED,
                Ticket.customer.in_(identity_keys_me) if identity_keys_me else Ticket.customer == current_user_name
            )
        )

        query = select(Ticket).where(filter_condition)

        count_query = select(func.count(Ticket.id)).where(filter_condition)
        total_result = await db.execute(count_query)
        total = total_result.scalar_one()

        skip = (page - 1) * size

        query = query.order_by(Ticket.created_at.desc()).offset(skip).limit(size)

        result = await db.execute(query)
        tickets = result.scalars().all()

        user_map = await TicketService._get_user_map(token)
        
        for ticket in tickets:
            setattr(ticket, "created_by_name", user_map.get(ticket.created_by, ticket.created_by))
            setattr(ticket, "reporter_name", user_map.get(ticket.created_by, ticket.created_by))
            if ticket.assigned_to:
                setattr(ticket, "assigned_to_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
                setattr(ticket, "assignee_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
            if ticket.customer:
                setattr(ticket, "customer_name", user_map.get(ticket.customer, ticket.customer))

        pages = (total + size - 1) // size

        return {
            "items": tickets,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages
        }

    @staticmethod
    async def get_ai_referee(title: str, comments: List[str], workload_map: Dict[str, int]) -> Dict[str, Any]:
        url = "http://localhost:9081/api/ticketReferee"
        data = {
            "title": title,
            "comments": comments,
            "workload_map": workload_map
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=data,
                    timeout=60.0
                )
                response.raise_for_status()
                result = response.json()
                return result
        except Exception as e:
            return {"code": 500, "message": f"AI服务调用失败: {str(e)}", "data": {}}

    @staticmethod
    async def assign_ticket_by_ai(db: AsyncSession, ticket: Ticket, token: Optional[str] = None) -> Dict[str, Any]:
        try:
            if ticket.assigned_to:
                return {"code": 200, "message": "工单已有处理人", "data": {"assigned_to": ticket.assigned_to}}
            
            workload_query = await db.execute(
                select(Ticket.assigned_to, func.count(Ticket.id))
                .where(Ticket.status != TicketStatus.CLOSED)
                .group_by(Ticket.assigned_to)
            )
            workload_result = workload_query.all()
            
            workload_map = {}
            for user_id, count in workload_result:
                if user_id:
                    user_map = await TicketService._get_user_map(token)
                    user_name = user_map.get(user_id, user_id)
                    workload_map[user_name] = count
            
            comments = []
            comment_query = await db.execute(
                select(TicketComment.content)
                .where(TicketComment.task_id == ticket.id)
                .order_by(TicketComment.created_at.asc())
                .limit(1)
            )
            comment_result = comment_query.scalar_one_or_none()
            if comment_result:
                comments.append(comment_result)
            
            ai_result = await TicketService.get_ai_referee(
                title=ticket.title,
                comments=comments,
                workload_map=workload_map
            )
            
            if ai_result.get("code") == 200 and ai_result.get("data", {}).get("name"):
                ai_assigned_name = ai_result["data"]["name"]
                user_map = await TicketService._get_user_map(token)
                reverse_user_map = {v: k for k, v in user_map.items()}
                ai_assigned_id = reverse_user_map.get(ai_assigned_name)
                
                if ai_assigned_id:
                    # 派单只写 assigned_to，不改状态——工单保持「新建」，由处理人「首次响应」后才进入「处理中」
                    ticket.assigned_to = ai_assigned_id
                    await db.commit()
                    operator = user_map.get(ticket.created_by, ticket.created_by)
                    await NotificationUtils.send_ticket_create_notification(
                        ticket.id, ticket.title, ticket.project_name, operator, ticket.deadline_at, [ai_assigned_id], token)
                    return {"code": 200, "message": "AI分配处理人成功", "data": {"assigned_to": ai_assigned_id, "assigned_to_name": ai_assigned_name}}
            
            return {"code": 400, "message": "AI分配处理人失败", "data": {}}
        except Exception as e:
            print(f"AI分配处理人失败: {str(e)}")
            return {"code": 500, "message": f"AI分配处理人失败: {str(e)}", "data": {}}

    @staticmethod
    async def trigger_ai_assignment(ticket_id: int, token: Optional[str] = None) -> Dict[str, Any]:
        try:
            from app.core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as async_db:
                ticket = await TicketService.get_ticket_by_id(async_db, ticket_id)
                if not ticket:
                    return {"code": 404, "message": "工单不存在", "data": {}}
                
                return await TicketService.assign_ticket_by_ai(async_db, ticket, token)
        except Exception as e:
            print(f"触发AI分配处理人失败: {str(e)}")
            return {"code": 500, "message": f"触发AI分配处理人失败: {str(e)}", "data": {}}

    @staticmethod
    async def get_user_ticket_stats(db: AsyncSession, username: str, near_deadline_hours: int = 24) -> Dict[str, Any]:
        now = datetime.now()
        near_deadline = now + timedelta(hours=near_deadline_hours)
        
        keys = TicketService._assignee_match_values(username)
        base_query = select(Ticket).where(Ticket.assigned_to.in_(keys) if keys else Ticket.assigned_to == username)
        
        pending_query = base_query.where(
            Ticket.status.in_([TicketStatus.NEW, TicketStatus.PENDING, TicketStatus.IN_PROGRESS])
        )
        pending_result = await db.execute(select(func.count()).select_from(pending_query.subquery()))
        pending_count = pending_result.scalar() or 0

        overdue_query = base_query.where(
            and_(
                Ticket.deadline_at.isnot(None),
                Ticket.deadline_at < now,
                Ticket.status.in_([TicketStatus.NEW, TicketStatus.PENDING, TicketStatus.IN_PROGRESS])
            )
        )
        overdue_result = await db.execute(select(func.count()).select_from(overdue_query.subquery()))
        overdue_count = overdue_result.scalar() or 0

        near_overdue_query = base_query.where(
            and_(
                Ticket.deadline_at.isnot(None),
                Ticket.deadline_at >= now,
                Ticket.deadline_at <= near_deadline,
                Ticket.status.in_([TicketStatus.NEW, TicketStatus.PENDING, TicketStatus.IN_PROGRESS])
            )
        )
        near_overdue_result = await db.execute(select(func.count()).select_from(near_overdue_query.subquery()))
        near_overdue_count = near_overdue_result.scalar() or 0

        total_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
        total_count = total_result.scalar() or 0

        status_stats = {}
        for status in TicketStatus:
            status_query = base_query.where(Ticket.status == status)
            status_result = await db.execute(select(func.count()).select_from(status_query.subquery()))
            status_count = status_result.scalar() or 0
            status_stats[status.value] = status_count
        
        return {
            "total": total_count,
            "pending_count": pending_count,
            "overdue_count": overdue_count,
            "near_overdue_count": near_overdue_count,
            "status_stats": status_stats
        }