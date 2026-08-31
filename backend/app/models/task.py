"""任务 ORM 模型（承 HelpDesk ticket → tasks 语义升格）。

MIGRATION.md Wave 2.2: 将工单(tickets/ticket_comments)重命名为任务(tasks/task_comments)，
落地 ARCHITECTURE.md「任务是统一抽象、工单是其类型」。

task_type 语义：problem/bug/feature/support/other

INTEGRATION_DESIGN.md Phase 1:
- Task 增加 source / external_id / external_url 字段 + (source, external_id) 唯一约束，
  支持外部任务源（禅道等）以插件方式接入，核心零感知具体源。
- 新增 TaskUserMapping：外部任务源账号 → 本平台 user_id 的跨源通用映射表。
"""
import enum

from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Enum as SQLEnum,
    BigInteger, Boolean, JSON, ForeignKey, desc, UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, mapped_column, Mapped

from app.models.base import Base


class TaskStatus(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"
    RESOLVED = "resolved"
    CANCELED = "canceled"
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


class OperationType(str, enum.Enum):
    """工单操作类型"""
    CREATE = "create"              # 创建工单
    STATUS_CHANGE = "status_change"  # 状态变更（主节点）
    ASSIGN = "assign"              # 派单/改派
    ESCALATE = "escalate"          # 升级
    RETURN = "return"              # 退回
    REASSIGN = "reassign"          # 重新指派
    UPDATE = "update"              # 修改字段
    COMMENT = "comment"            # 添加评论
    VIEW = "view"                  # 查看工单
    AI_DIAGNOSE = "ai_diagnose"    # AI 诊断
    AI_ASSIGN = "ai_assign"        # AI 派单


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
    assigned_to = Column(String(50), nullable=True, index=True, comment="处理者ID（users.id）")
    customer = Column(String(100), nullable=True, comment="客户信息")
    team = Column(String(100), nullable=True, comment="所属团队")
    project_name = Column(String(255), nullable=True, index=True, comment="项目名称")
    project_id = Column(String(255), nullable=True, index=True, comment="项目ID")
    related_resource_id = Column(BigInteger, nullable=True, index=True, comment="关联资源ID")

    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), nullable=False, comment="更新时间")
    resolved_at = Column(DateTime, nullable=True, comment="解决时间")
    canceled_at = Column(DateTime, nullable=True, comment="取消时间")
    closed_at = Column(DateTime, nullable=True, comment="关闭时间")
    deadline_at = Column(DateTime, nullable=True, comment="截止时间")

    tags = Column(JSON, nullable=True, comment="标签列表")
    metadata_info = Column(JSON, nullable=True, comment="扩展元数据")
    attachments = Column(JSON, nullable=True, comment="附件列表")
    attachment_analysis = Column(JSON, nullable=True,
                                  comment="附件分析记忆：{object_path: {filename, kind, summary, analyzed_at}}，供 AI 判断每次需重新分析的附件，避免重复分析")

    reply_count = Column(Integer, nullable=False, default=0, comment="回复数量")
    view_count = Column(Integer, nullable=False, default=0, comment="查看数量")

    # --- 外部任务源（插件化，见 INTEGRATION_DESIGN.md）---
    source = Column(String(32), nullable=False, default="manual", index=True,
                    comment="任务来源：manual / zentao / ...")
    external_id = Column(String(64), nullable=True, index=True, comment="外部系统任务ID")
    external_url = Column(String(512), nullable=True, comment="外部系统跳转链接")

    # --- 当前步骤（关联 task_steps 模板，冗余存名称/结束时间便于直接展示）---
    curr_step_id = Column(BigInteger, nullable=True, index=True, comment="当前步骤ID")
    curr_step_name = Column(String(128), nullable=True, comment="当前步骤名称")
    curr_step_endtime = Column(DateTime, nullable=True, comment="当前步骤结束时间")

    __table_args__ = (
        # MySQL 允许多个 NULL，故 manual 任务（external_id=NULL）不冲突
        UniqueConstraint("source", "external_id", name="uq_task_source_external"),
    )

    def __repr__(self):
        return f"<Task(id={self.id}, title='{self.title}', status={self.status})>"

    @property
    def is_open(self) -> bool:
        return self.status in [TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.PENDING]

    @property
    def is_resolved(self) -> bool:
        return self.status == TaskStatus.RESOLVED

    @property
    def is_canceled(self) -> bool:
        return self.status == TaskStatus.CANCELED

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
    reply_to = Column(BigInteger, nullable=True, index=True, comment="引用的评论ID（消息引用/回复）")

    task = relationship("Task", backref="comments", order_by=lambda: desc(TaskComment.created_at))

    @property
    def ticket_id(self) -> int:
        return self.task_id

    @ticket_id.setter
    def ticket_id(self, value: int) -> None:
        self.task_id = value

    def __repr__(self):
        return f"<TaskComment(id={self.id}, task_id={self.task_id}, created_by='{self.created_by}')>"


class TaskCommentRead(Base):
    """评论已读游标（轻量 IM 已读回执）：每用户每工单记录已读到的最后一条评论 id。"""
    __tablename__ = "task_comment_read"

    id = Column(BigInteger, primary_key=True, index=True, comment="已读记录ID")
    task_id = Column(BigInteger, nullable=False, index=True, comment="任务ID")
    username = Column(String(50), nullable=False, index=True, comment="用户username")
    last_read_comment_id = Column(BigInteger, nullable=False, comment="已读到的最后一条评论ID")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

    __table_args__ = (
        UniqueConstraint("task_id", "username", name="uq_task_read_user"),
    )

    def __repr__(self):
        return f"<TaskCommentRead(task_id={self.task_id}, username='{self.username}', last_read={self.last_read_comment_id})>"


class TaskCommentReadRecord(Base):
    """单条评论的已读记录（飞书式已读名单）：谁在何时读了哪条评论。

    与 TaskCommentRead（游标）互补：游标用于快速算「读到哪」，本表用于
    「每条消息的已读人员名单 + 按阅读时间排序」。唯一键 (comment_id, username) 幂等。
    """
    __tablename__ = "task_comment_read_record"

    id = Column(BigInteger, primary_key=True, index=True, comment="已读明细ID")
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True, comment="任务ID")
    comment_id = Column(BigInteger, nullable=False, index=True, comment="评论ID")
    username = Column(String(50), nullable=False, index=True, comment="读者username")
    read_at = Column(DateTime, server_default=func.now(), nullable=False, comment="阅读时间")

    __table_args__ = (
        UniqueConstraint("comment_id", "username", name="uq_comment_read_user"),
    )

    def __repr__(self):
        return f"<TaskCommentReadRecord(comment_id={self.comment_id}, username='{self.username}', read_at={self.read_at})>"


class TaskUserMapping(Base):
    """外部任务源账号 → 本平台 user_id 的映射（跨源通用，见 INTEGRATION_DESIGN.md §4.3）。

    SyncEngine 落库时按 (source, external_account) 查本表解析处理人/创建人。
    """
    __tablename__ = "task_user_mapping"

    id = Column(BigInteger, primary_key=True, index=True, comment="映射ID")
    source = Column(String(32), nullable=False, index=True, comment="任务源：zentao / ...")
    external_account = Column(String(64), nullable=False, comment="外部系统账号，如禅道 account")
    external_realname = Column(String(128), nullable=True, comment="外部账号姓名，便于识别")
    local_user_id = Column(String(50), nullable=False, index=True, comment="本平台 user_id")

    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

    __table_args__ = (
        UniqueConstraint("source", "external_account", name="uq_mapping_src_account"),
    )

    def __repr__(self):
        return f"<TaskUserMapping(source={self.source}, {self.external_account} -> {self.local_user_id})>"


class TaskOperationLog(Base):
    """工单操作日志表"""
    __tablename__ = "task_operation_logs"

    id = Column(BigInteger, primary_key=True, index=True, comment="日志ID")
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True, comment="任务ID")
    operation_type = Column(SQLEnum(OperationType), nullable=False,
                            index=True, comment="操作类型")
    operator = Column(String(50), nullable=False, index=True,
                      comment="操作人 username")
    operator_name = Column(String(128), nullable=True, comment="操作人显示名")

    # 状态变更专属：记录目标状态（用于主节点分组）
    to_status = Column(String(32), nullable=True, index=True,
                       comment="目标状态（仅 STATUS_CHANGE 有值）")

    # 通用详情：JSON 存储操作快照
    # 如 {"from": "new", "to": "in_progress"} 或 {"fields": ["title","priority"]}
    detail = Column(JSON, nullable=True, comment="操作详情快照")
    description = Column(String(500), nullable=True,
                         comment="人类可读描述，如：将工单状态变更为「处理中」")

    # 查看时长专属（仅 VIEW 操作有值）：前端在用户离开页面时回传累计停留秒数
    ended_at = Column(DateTime, nullable=True, comment="查看结束时间（仅 VIEW 有值）")
    duration_seconds = Column(Integer, nullable=True, comment="查看时长（秒，仅 VIEW 有值）")

    created_at = Column(DateTime, server_default=func.now(),
                        nullable=False, index=True, comment="操作时间")

    task = relationship("Task", backref="operation_logs")

    def __repr__(self):
        return f"<TaskOperationLog(id={self.id}, task_id={self.task_id}, op={self.operation_type})>"


class TaskStep(Base):
    """任务步骤模板：按 task_type 预定义的处理步骤（每类型可有多步）。

    与 Task.task_type 共用 TaskType 枚举语义；用于驱动标准化处理流程
    （如创建任务时按类型展开步骤清单）。
    """
    __tablename__ = "task_steps"

    id = Column(BigInteger, primary_key=True, index=True, comment="步骤ID")
    task_type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType), nullable=False, index=True, comment="任务类型")
    step_name = Column(String(128), nullable=False, comment="步骤名称")
    sequence = Column(Integer, nullable=False, server_default="0", comment="当前步骤在当前任务类型下的序号")

    def __repr__(self):
        return f"<TaskStep(id={self.id}, task_type={self.task_type}, sequence={self.sequence}, step_name='{self.step_name}')>"
