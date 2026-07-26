"""任务模型——再导出 shim（MIGRATION.md Wave 2.2）。

真实 ORM 定义已从 `app/models/ticket.py` 迁至 `app/models/task.py`。
保持旧导入路径可用，同时提供新命名的别名。

旧导入仍可用（向后兼容）：
    from app.modules.tasks.models.ticket import Ticket, TicketComment, ...

新命名也可用：
    from app.modules.tasks.models.ticket import Task, TaskComment, ...
"""
from app.models.task import (
    Task,
    TaskComment,
    TaskStatus,
    TaskPriority,
    TaskType,
)

Ticket = Task
TicketComment = TaskComment
TicketStatus = TaskStatus
TicketPriority = TaskPriority
TicketType = TaskType

__all__ = [
    "Task",
    "TaskComment",
    "TaskStatus",
    "TaskPriority",
    "TaskType",
    "Ticket",
    "TicketComment",
    "TicketStatus",
    "TicketPriority",
    "TicketType",
]