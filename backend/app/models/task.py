"""任务 ORM 模型（承 HelpDesk ticket → tasks 语义升格）。

MIGRATION.md Wave 2.2: 将工单(tickets/ticket_comments)重命名为任务(tasks/task_comments)，
落地 ARCHITECTURE.md「任务是统一抽象、工单是其类型」。

task_type 语义：problem/bug/feature/support/other
"""
import enum

from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Enum as SQLEnum,
    BigInteger, Boolean, JSON, ForeignKey, desc,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, mapped_column, Mapped

from app.models.base import Base


class TaskStatus(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TaskPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskType(str, enum.Enum):
    PROBLEM = "problem"
    FEATURE = "feature"
    BUG = "bug"
    SUPPORT = "support"
    OTHER = "other"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(BigInteger, primary_key=True, index=True, comment="任务ID")
    title = Column(String(255), nullable=False, index=True, comment="任务标题")
    description = Column(Text, nullable=False, comment="任务描述")
    task_type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType), nullable=False, default=TaskType.PROBLEM, index=True, comment="任务类型")

    @property
    def ticket_type(self) -> TaskType:
        return self.task_type

    @ticket_type.setter
    def ticket_type(self, value: TaskType) -> None:
        self.task_type = value

    status: Mapped[TaskStatus] = mapped_column(SQLEnum(TaskStatus), nullable=False, default=TaskStatus.NEW, index=True, comment="任务状态")
    priority: Mapped[TaskPriority] = mapped_column(SQLEnum(TaskPriority), nullable=False, default=TaskPriority.MEDIUM, index=True, comment="任务优先级")

    created_by = Column(String(50), nullable=False, index=True, comment="创建者ID")
    assigned_to = Column(String(50), nullable=True, index=True, comment="处理者ID")
    customer = Column(String(100), nullable=True, comment="客户信息")
    team = Column(String(100), nullable=True, comment="所属团队")
    project_name = Column(String(255), nullable=True, index=True, comment="项目名称")
    project_id = Column(String(255), nullable=True, index=True, comment="项目ID")
    related_resource_id = Column(BigInteger, nullable=True, index=True, comment="关联资源ID")

    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), nullable=False, comment="更新时间")
    resolved_at = Column(DateTime, nullable=True, comment="解决时间")
    closed_at = Column(DateTime, nullable=True, comment="关闭时间")
    deadline_at = Column(DateTime, nullable=True, comment="截止时间")

    tags = Column(JSON, nullable=True, comment="标签列表")
    metadata_info = Column(JSON, nullable=True, comment="扩展元数据")
    attachments = Column(JSON, nullable=True, comment="附件列表")

    reply_count = Column(Integer, nullable=False, default=0, comment="回复数量")
    view_count = Column(Integer, nullable=False, default=0, comment="查看数量")

    def __repr__(self):
        return f"<Task(id={self.id}, title='{self.title}', status={self.status})>"

    @property
    def is_open(self) -> bool:
        return self.status in [TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.PENDING]

    @property
    def is_resolved(self) -> bool:
        return self.status == TaskStatus.RESOLVED

    @property
    def is_closed(self) -> bool:
        return self.status == TaskStatus.CLOSED


class TaskComment(Base):
    __tablename__ = "task_comments"

    id = Column(BigInteger, primary_key=True, index=True, comment="评论ID")
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True, comment="任务ID")
    content = Column(Text, nullable=False, comment="评论内容")

    created_by = Column(String(50), nullable=False, index=True, comment="创建者ID")

    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

    is_public = Column(Boolean, nullable=False, default=True, comment="是否公开")
    attachments = Column(JSON, nullable=True, comment="附件列表")

    task = relationship("Task", backref="comments", order_by=lambda: desc(TaskComment.created_at))

    @property
    def ticket_id(self) -> int:
        return self.task_id

    @ticket_id.setter
    def ticket_id(self, value: int) -> None:
        self.task_id = value

    def __repr__(self):
        return f"<TaskComment(id={self.id}, task_id={self.task_id}, created_by='{self.created_by}')>"