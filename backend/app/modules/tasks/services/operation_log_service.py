"""工单操作日志服务

统一管理工单操作的记录与查询。
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_
from sqlalchemy.sql import func

from app.models.task import TaskOperationLog, OperationType
from app.core.database import db_manager

logger = logging.getLogger(__name__)

# 查看记录去重时间窗口（秒）
VIEW_DEDUP_WINDOW = 300  # 5 分钟


def get_role_prefix(
    created_by: Optional[str],
    assigned_to: Optional[str],
    operator: str,
) -> str:
    """返回操作人在工单中的角色前缀。

    根据工单的 created_by / assigned_to 判断操作人是创建人还是处理人，
    返回形如 '【创建人】' 的前缀；若两者都不是则返回空字符串。
    """
    is_creator = bool(created_by) and operator == created_by
    is_assignee = bool(assigned_to) and operator == assigned_to
    if is_creator and is_assignee:
        return "【创建人/处理人】"
    if is_creator:
        return "【创建人】"
    if is_assignee:
        return "【处理人】"
    return ""


def _resolve_operator_name(operator: str, operator_name: Optional[str]) -> str:
    """通过 username 解析显示名。

    传入的 operator_name 可能是 JWT fallback 的 username（无效），需要继续查库。
    只有传入值与 operator 不同时才认为是有效显示名。
    """
    if operator_name and operator_name != operator:
        logger.info(f"[OpLog] _resolve: operator={operator}, using passed operator_name={operator_name}")
        return operator_name
    # 传入为空或等于 username（JWT fallback），查用户表补全
    try:
        user = db_manager.get_user(operator)
        if user:
            user_name = user.get("name")
            logger.info(f"[OpLog] _resolve: operator={operator}, db_user.name={user_name!r}")
            if user_name:
                return user_name
        else:
            logger.warning(f"[OpLog] _resolve: operator={operator}, db_manager.get_user returned None")
    except Exception as e:
        logger.warning(f"[OpLog] _resolve: operator={operator}, exception: {e}", exc_info=True)
    logger.info(f"[OpLog] _resolve: operator={operator}, fallback to username")
    return operator


class OperationLogService:
    """工单操作日志服务类"""

    @staticmethod
    async def log(
        db: AsyncSession,
        task_id: int,
        op_type: OperationType,
        operator: str,
        operator_name: Optional[str] = None,
        to_status: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Optional[TaskOperationLog]:
        """
        统一写入操作日志。

        Args:
            db: 数据库会话
            task_id: 工单 ID
            op_type: 操作类型
            operator: 操作人 username
            operator_name: 操作人显示名（未传时自动查用户表补全）
            to_status: 目标状态（仅 STATUS_CHANGE 有值）
            detail: 操作详情快照（JSON 可序列化的 dict）
            description: 人类可读描述

        Returns:
            创建的 TaskOperationLog 实例，失败返回 None
        """
        try:
            resolved_name = _resolve_operator_name(operator, operator_name)
            log_entry = TaskOperationLog(
                task_id=task_id,
                operation_type=op_type,
                operator=operator,
                operator_name=resolved_name,
                to_status=to_status,
                detail=detail,
                description=description or f"{resolved_name} {op_type.value}",
            )
            db.add(log_entry)
            await db.commit()
            await db.refresh(log_entry)
            logger.info(f"Operation logged: task_id={task_id}, type={op_type.value}, operator={operator}")
            return log_entry
        except Exception as e:
            logger.error(f"Failed to log operation for task {task_id}: {str(e)}", exc_info=True)
            await db.rollback()
            return None

    @staticmethod
    async def list_by_task(
        db: AsyncSession,
        task_id: int,
    ) -> List[TaskOperationLog]:
        """
        按时间倒序返回工单的全部操作记录。

        Args:
            db: 数据库会话
            task_id: 工单 ID

        Returns:
            操作日志列表，按创建时间倒序
        """
        try:
            query = select(TaskOperationLog).where(
                TaskOperationLog.task_id == task_id
            ).order_by(desc(TaskOperationLog.created_at))

            result = await db.execute(query)
            logs = result.scalars().all()
            return list(logs)
        except Exception as e:
            logger.error(f"Failed to list operation logs for task {task_id}: {str(e)}", exc_info=True)
            return []

    @staticmethod
    async def log_view(
        db: AsyncSession,
        task_id: int,
        username: str,
        user_name: Optional[str] = None,
        ticket_created_by: Optional[str] = None,
        ticket_assigned_to: Optional[str] = None,
    ) -> Optional[TaskOperationLog]:
        """
        记录查看工单操作（带去重：同一用户在 VIEW_DEDUP_WINDOW 秒内重复查看只记一次）。

        Args:
            db: 数据库会话
            task_id: 工单 ID
            username: 操作人 username
            user_name: 操作人显示名
            ticket_created_by: 工单创建人 username（用于角色判断）
            ticket_assigned_to: 工单处理人 username（用于角色判断）

        Returns:
            创建的 TaskOperationLog 实例，去重或失败返回 None
        """
        try:
            # 去重：查询最近时间窗口内是否已有同一用户的 VIEW 记录
            cutoff_time = datetime.now() - timedelta(seconds=VIEW_DEDUP_WINDOW)
            logger.info(f"[OpLog] log_view: task_id={task_id}, username={username}, passed user_name={user_name!r}")
            dedup_query = select(TaskOperationLog).where(
                and_(
                    TaskOperationLog.task_id == task_id,
                    TaskOperationLog.operator == username,
                    TaskOperationLog.operation_type == OperationType.VIEW,
                    TaskOperationLog.created_at >= cutoff_time,
                )
            ).limit(1)
            result = await db.execute(dedup_query)
            if result.scalar_one_or_none():
                logger.info(f"[OpLog] log_view deduped: task_id={task_id}, operator={username}")
                return None  # 去重，不重复记录

            # 写入新的查看记录（log 内部会自动补全 operator_name）
            resolved = _resolve_operator_name(username, user_name)
            role = get_role_prefix(ticket_created_by, ticket_assigned_to, username)
            logger.info(f"[OpLog] log_view writing: task_id={task_id}, operator={username}, resolved_name={resolved!r}, role={role!r}")
            return await OperationLogService.log(
                db=db,
                task_id=task_id,
                op_type=OperationType.VIEW,
                operator=username,
                operator_name=user_name,
                description=f"{role}{resolved} 查看了工单" if role else f"{resolved} 查看了工单",
            )
        except Exception as e:
            logger.error(f"Failed to log view for task {task_id}: {str(e)}", exc_info=True)
            return None
