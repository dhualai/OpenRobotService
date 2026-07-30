from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.attributes import set_committed_value

from app.modules.tasks.models.ticket import Ticket, TicketComment, TicketStatus, TicketPriority, TicketType
from app.modules.tasks.schemas.ticket import TicketCreate, TicketUpdate, TicketCommentCreate, TicketCommentUpdate, TicketQueryParams, TicketFilterRequest
from app.core.config import settings
from app.utils.notification_utils import NotificationUtils
from app.utils.image_processor import ImageProcessor
from app.services.user_service import user_service


def convert_to_shanghai_time(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    
    if dt.tzinfo is not None:
        utc_dt = dt.astimezone(timezone.utc)
        shanghai_dt = utc_dt + timedelta(hours=8)
        return shanghai_dt.replace(tzinfo=None)
    
    return dt


def is_valid_id(id_value):
    return isinstance(id_value, int) and id_value > 0


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
        
        return matched_ids

    @staticmethod
    async def create_ticket(db: AsyncSession, ticket_data: TicketCreate, created_by: str, comment_attachment_map: dict, token: Optional[str] = None) -> Ticket:
        processed_attachments = []
        for attachment in ticket_data.attachments or []:
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

        user_map = await TicketService._get_user_map(token)
        created_by_name = user_map.get(created_by, created_by)

        async with db.begin():
            db_ticket = Ticket(
                title=ticket_data.title,
                description=processed_description,
                ticket_type=ticket_data.ticket_type,
                priority=ticket_data.priority,
                related_resource_id=ticket_data.related_resource_id,
                created_by=created_by,
                tags=ticket_data.tags,
                metadata_info=ticket_data.metadata_info,
                project_name=ticket_data.project_name,
                project_id=ticket_data.project_id,
                deadline_at=convert_to_shanghai_time(ticket_data.deadline_at),
                assigned_to=created_by,
                customer=ticket_data.customer,
                status=TicketStatus.NEW
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
            query = TicketService._apply_string_op(
                query, Ticket.created_by, query_params.created_by, query_params.created_by_op, 'equals')

        if query_params.created_by_name:
            matched_ids = await TicketService._get_user_ids_by_name(query_params.created_by_name, token)
            if matched_ids:
                query = query.where(Ticket.created_by.in_(matched_ids))

        if query_params.assigned_to:
            query = TicketService._apply_string_op(
                query, Ticket.assigned_to, query_params.assigned_to, query_params.assigned_to_op, 'equals')

        if query_params.assigned_to_name:
            matched_ids = await TicketService._get_user_ids_by_name(query_params.assigned_to_name, token)
            if matched_ids:
                query = query.where(Ticket.assigned_to.in_(matched_ids))

        if query_params.customer:
            query = TicketService._apply_string_op(
                query, Ticket.customer, query_params.customer, query_params.customer_op, 'equals')

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
            if op == 'contains':
                return query.where(column.ilike(f"%{value}%"))
            elif op == 'not_contains':
                return query.where(~column.ilike(f"%{value}%"))
            elif op == 'eq':
                return query.where(column == value)
            elif op == 'ne':
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
            'ticketType': (Ticket.ticket_type, 'enum'),
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
            setattr(ticket, "created_by_name", user_map.get(ticket.created_by, ticket.created_by))
            setattr(ticket, "reporter_name", user_map.get(ticket.created_by, ticket.created_by))
            if ticket.assigned_to:
                setattr(ticket, "assigned_to_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
                setattr(ticket, "assignee_name", user_map.get(ticket.assigned_to, ticket.assigned_to))
            if ticket.customer:
                setattr(ticket, "customer_name", user_map.get(ticket.customer, ticket.customer))
        
        if ticket and load_comments:
            logger.info(f"开始加载评论: ticket_id={ticket_id}")
            try:
                from sqlalchemy.orm import joinedload
                result = await db.execute(
                    select(Ticket)
                    .where(Ticket.id == ticket_id)
                    .options(joinedload(Ticket.comments))
                )
                ticket = result.unique().scalar_one_or_none()

                if ticket:
                    user_map = await TicketService._get_user_map(token)
                    for comment in ticket.comments:
                        setattr(comment, "created_by_name", user_map.get(comment.created_by, comment.created_by))
                        content = comment.content
                        comment.content = ImageProcessor.process_content_for_response(content)

                logger.info(f"评论加载成功: ticket_id={ticket_id}, comment_count={len(ticket.comments) if ticket else 0}")
            except Exception as e:
                logger.error(f"评论加载失败: ticket_id={ticket_id}, error={str(e)}", exc_info=True)
                raise
            finally:
                # 评论加载完成后设 committed_value，避免后续序列化时触发 lazy load → MissingGreenlet
                if ticket:
                    set_committed_value(ticket, 'comments', getattr(ticket, 'comments', []))
        elif ticket:
            # 未请求评论时也设空列表，避免序列化时触发 lazy load → MissingGreenlet
            set_committed_value(ticket, 'comments', [])
        return ticket

    @staticmethod
    async def update_ticket(db: AsyncSession, ticket_id: int, ticket_update: TicketUpdate, token: Optional[str] = None, operator_id: Optional[str] = None) -> Dict[str, Any]:
        ticket = await TicketService.get_ticket_by_id(db, ticket_id)
        if not ticket:
            return {"ticket": None, "notification": None}

        update_data = ticket_update.dict(exclude_unset=True)

        for field, value in update_data.items():
            if field == "deadline_at":
                value = convert_to_shanghai_time(value)
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
            if notify_users:
                user_map = await TicketService._get_user_map(token)
                
                update_details = []
                for field, value in update_data.items():
                    if isinstance(value, list):
                        value_str = ', '.join(str(item) for item in value)
                    else:
                        value_str = str(value)
                    
                    if field in ['assigned_to', 'customer']:
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
            import logging
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
    async def add_comment(db: AsyncSession, ticket_id: int, comment_data: TicketCommentCreate, created_by: str, comment_attachment_map: dict) -> Optional[TicketComment]:
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
            created_by=created_by
        )
        
        ticket.reply_count = ticket.reply_count + 1
        
        db.add(comment)
        await db.commit()
        await db.refresh(comment)
        await db.refresh(ticket)
        
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
            setattr(comment, "created_by_name", user_map.get(comment.created_by, comment.created_by))
            try:
                content = comment.content
                logger.info(f"开始处理评论内容: comment_id={comment.id}, content_length={len(content) if content else 0}")
                processed_content = ImageProcessor.process_content_for_response(content)
                comment.content = processed_content
                logger.info(f"评论内容处理成功: comment_id={comment.id}")
            except Exception as e:
                logger.error(f"评论内容处理失败: comment_id={comment.id}, error={str(e)}", exc_info=True)
                raise
        
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

        comment.updated_at = datetime.now()
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
    async def update_ticket_status(db: AsyncSession, ticket_id: int, status: TicketStatus, token: Optional[str] = None, operator_id: Optional[str] = None) -> Optional[Ticket]:
        ticket = await TicketService.get_ticket_by_id(db, ticket_id)
        if not ticket:
            return None
        
        ticket.status = status
        
        if status == TicketStatus.RESOLVED:
            ticket.resolved_at = func.now()
        elif status == TicketStatus.CLOSED:
            ticket.closed_at = func.now()
        
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
            import logging
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

        ticket.assigned_to = user_id
        ticket.status = TicketStatus.IN_PROGRESS

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
        filter_condition = or_(
            and_(
                Ticket.status == TicketStatus.NEW,
                Ticket.created_by == current_user_name
            ),
            and_(
                Ticket.status.in_([TicketStatus.IN_PROGRESS, TicketStatus.PENDING]),
                Ticket.assigned_to == current_user_name
            ),
            and_(
                Ticket.status == TicketStatus.RESOLVED,
                Ticket.customer == current_user_name
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
                    ticket.assigned_to = ai_assigned_id
                    ticket.status = TicketStatus.IN_PROGRESS
                    await db.commit()
                    await NotificationUtils.send_ticket_create_notification(
                        ticket.id, ticket.title, ticket.project_name, "AI自动派单", [ai_assigned_id], token)
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
        
        base_query = select(Ticket).where(Ticket.assigned_to == username)
        
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