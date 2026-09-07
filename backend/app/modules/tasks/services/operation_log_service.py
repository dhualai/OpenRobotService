"""工单操作日志服务

统一管理工单操作的记录与查询。
"""
import logging
from typing import Optional, List, Dict, Any
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, text
from sqlalchemy.sql import func

from app.models.task import TaskOperationLog, OperationType
from app.core.database import db_manager, AsyncSessionLocal
from app.core.user_identity import same_identity

logger = logging.getLogger(__name__)

# 查看记录去重时间窗口（秒）
VIEW_DEDUP_WINDOW = 300  # 5 分钟

# 查看日志写入的进程内串行锁：去重是「SELECT 窗口内记录 → 无则 INSERT」两步，
# 并发的详情请求会在彼此提交前都查不到记录，导致同一用户同一秒插入重复 VIEW 行。
# 按 (task_id, operator) 加锁把检查+写入串行化（单 worker 部署即可消除竞态）。
_view_locks: Dict[tuple, asyncio.Lock] = {}


def _get_view_lock(task_id: int, username: str) -> asyncio.Lock:
    key = (task_id, username)
    lock = _view_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _view_locks[key] = lock
    return lock


def get_role_prefix(
    created_by: Optional[str],
    assigned_to: Optional[str],
    operator: str,
) -> str:
    """返回操作人在工单中的角色前缀。

    根据工单的 created_by / assigned_to 判断操作人是创建人还是处理人，
    返回形如 '【创建人】' 的前缀；若两者都不是则返回空字符串。
    """
    is_creator = same_identity(operator, created_by)
    is_assignee = same_identity(operator, assigned_to)
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
            # 同一工单 + 同一用户的查看日志写入串行化，消除并发请求下
            # 「都查不到窗口内记录 → 都插入」的竞态
            async with _get_view_lock(task_id, username):
                # 去重检查与写入均使用独立短会话：
                # 1. 独立会话拥有新的事务读视图，能读到其它并发请求刚提交的 VIEW 行
                #    （调用方 db 会话此前已执行查询，REPEATABLE READ 下读视图固定）；
                # 2. 不触碰调用方会话——rollback/close 会 expire 已加载的 ticket 对象，
                #    导致后续响应序列化时异步懒加载失败（MissingGreenlet → 500）。
                async with AsyncSessionLocal() as dedup_db:
                    # 窗口边界必须与 created_at 同源：created_at 由 MySQL NOW() 填充
                    # （服务器会话时区，可能为 UTC），而 Python datetime.now() 是 OS 本地时区。
                    # 混用会导致比较恒不成立、去重失效，因此用 DATE_SUB(NOW(), ...) 在 DB 端计算。
                    # VIEW_DEDUP_WINDOW 为内部常量，可直接内联。
                    cutoff_expr = func.date_sub(func.now(), text(f'INTERVAL {VIEW_DEDUP_WINDOW} SECOND'))
                    logger.info(f"[OpLog] log_view: task_id={task_id}, username={username}, passed user_name={user_name!r}")
                    dedup_query = select(TaskOperationLog).where(
                        and_(
                            TaskOperationLog.task_id == task_id,
                            TaskOperationLog.operator == username,
                            TaskOperationLog.operation_type == OperationType.VIEW,
                            TaskOperationLog.created_at >= cutoff_expr,
                        )
                    ).limit(1)
                    result = await dedup_db.execute(dedup_query)
                    if result.scalar_one_or_none():
                        logger.info(f"[OpLog] log_view deduped: task_id={task_id}, operator={username}")
                        return None  # 去重，不重复记录

                    # 写入新的查看记录（独立会话，log 内部自动补全 operator_name 并提交）
                    resolved = _resolve_operator_name(username, user_name)
                    role = get_role_prefix(ticket_created_by, ticket_assigned_to, username)
                    logger.info(f"[OpLog] log_view writing: task_id={task_id}, operator={username}, resolved_name={resolved!r}, role={role!r}")
                    return await OperationLogService.log(
                        db=dedup_db,
                        task_id=task_id,
                        op_type=OperationType.VIEW,
                        operator=username,
                        operator_name=user_name,
                        description=f"{role}{resolved} 查看了工单" if role else f"{resolved} 查看了工单",
                    )
        except Exception as e:
            logger.error(f"Failed to log view for task {task_id}: {str(e)}", exc_info=True)
            return None

    @staticmethod
    async def update_view_duration(
        db: AsyncSession,
        task_id: int,
        username: str,
        duration_seconds: int,
    ) -> bool:
        """更新最近一条查看记录的停留时长。

        前端在用户离开页面时回传累计停留秒数，后端将其累加到最近一条 VIEW 记录上
        （多次可见性切换/短时离开再回来会在同一条 VIEW 记录上累加）。

        Args:
            db: 数据库会话
            task_id: 工单 ID
            username: 操作人 username
            duration_seconds: 本次回传的停留秒数

        Returns:
            是否更新成功
        """
        if duration_seconds <= 0:
            return False
        try:
            # 查找该用户在本工单上最近一条 VIEW 记录
            query = select(TaskOperationLog).where(
                and_(
                    TaskOperationLog.task_id == task_id,
                    TaskOperationLog.operator == username,
                    TaskOperationLog.operation_type == OperationType.VIEW,
                )
            ).order_by(desc(TaskOperationLog.created_at)).limit(1)
            result = await db.execute(query)
            log = result.scalar_one_or_none()
            if not log:
                logger.warning(
                    f"[OpLog] update_view_duration: no VIEW record found for "
                    f"task_id={task_id}, operator={username}"
                )
                return False

            # 累加停留时长（多次回传累加，覆盖同一查看会话的多个可见性周期）
            existing = log.duration_seconds or 0
            log.duration_seconds = existing + duration_seconds
            # 与 created_at 同源（MySQL NOW()），避免 Python 本地时区与 DB 时区差 8 小时
            log.ended_at = func.now()
            await db.commit()
            logger.info(
                f"[OpLog] update_view_duration: task_id={task_id}, operator={username}, "
                f"added={duration_seconds}s, total={log.duration_seconds}s"
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to update view duration for task {task_id}: {str(e)}",
                exc_info=True,
            )
            await db.rollback()
            return False
