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
    BigInteger, Boolean, JSON, ForeignKey, desc, UniqueConstraint, text,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, mapped_column, Mapped
from sqlalchemy.ext.hybrid import hybrid_property

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
    step_last_updated_by = Column(String(100), nullable=True, comment="最近一次改step的操作人：assigned/creator侧标识，用于判定待处理回合")
    step_last_updated_at = Column(DateTime, nullable=True, comment="最近一次step更新时间")
    step_negotiation_round = Column(Integer, nullable=False, server_default="0", default=0, comment="协商回合数：初始0，对手回应一次+1")
    step_phase_round = Column(Integer, nullable=False, server_default="0", default=0, comment="阶段回合数：complete-step 推进+1，初始0=第一轮；0时协商节点不受sequence下限限制")
    curr_step_agreed = Column(Boolean, nullable=False, server_default="0", default=False,
                              comment="当前协商节点是否已协商一致：respond 置 True；negotiate-step/complete-step 重置为 False")
    escalate_count = Column(Integer, nullable=False, server_default="0", default=0,
                        comment="升级上报次数：>0 表示已升级，协商回合重置为1且不再受限")

    __table_args__ = (
        # MySQL 允许多个 NULL，故 manual 任务（external_id=NULL）不冲突
        UniqueConstraint("source", "external_id", name="uq_task_source_external"),
    )

    def __repr__(self):
        return f"<Task(id={self.id}, title='{self.title}', status={self.status})>"

    @hybrid_property
    def step_neg_max_rounds(self) -> int:
        """协商回合上限。工单未指定专属上限时读取全局配置。"""
        # 延迟引入避免循环依赖
        from app.core.config import settings as _s
        return _s.TICKET_STEP_MAX_NEGOTIATION_ROUNDS

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


class TaskFollower(Base):
    """任务关注表：用户主动关注（卡片星标）的工单。

    与 TaskCommentRead 同构：task_id + username 唯一键保证幂等，
    重复关注只刷新 created_at，不产生脏数据。username 口径与
    task_comment_read.username 一致（当前登录用户）。
    """
    __tablename__ = "task_followers"

    id = Column(BigInteger, primary_key=True, index=True, comment="关注记录ID")
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True, comment="任务ID")
    username = Column(String(50), nullable=False, index=True, comment="关注人username")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="关注时间")

    __table_args__ = (
        UniqueConstraint("task_id", "username", name="uq_task_followers"),
    )

    def __repr__(self):
        return f"<TaskFollower(task_id={self.task_id}, username='{self.username}')>"


class TaskParticipant(Base):
    """任务参与人表：记录与工单相关度较高的多个人（工单视角）。

    与 TaskFollower 同构但语义不同：
      - follower 是「用户视角」——我主动关注了哪些工单（卡片星标）；
      - participant 是「工单视角」——这工单有哪些相关人（评论/附件等业务事件触发）。

    当前写入触发点：评论端点（POST /{task_id}/comments）成功后同事务幂等 upsert。
    后续可扩展派单/被@等触发点。同一 (task_id, username) 唯一，重复参与只刷新时间。
    """
    __tablename__ = "task_participants"

    id = Column(BigInteger, primary_key=True, index=True, comment="参与记录ID")
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True, comment="任务ID")
    username = Column(String(50), nullable=False, index=True, comment="参与人username")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="首次参与时间")
    last_active_at = Column(DateTime, server_default=func.now(), nullable=False,
                            comment="最近一次参与时间（重复参与时刷新）")

    __table_args__ = (
        UniqueConstraint("task_id", "username", name="uq_task_participants"),
    )

    def __repr__(self):
        return f"<TaskParticipant(task_id={self.task_id}, username='{self.username}')>"


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


class TaskSpecDoc(Base):
    """工单「完整问题文档」：提单人结构化描述问题 + 接单人补充，md 在线编辑。

    独立于 tasks 表：AI 侧 upsert_task 会整体替换 metadata_info，文档若存
    metadata_info/description 会被反复覆盖（设计评审结论），故单列一张表。
    一工单一文档：UNIQUE(task_id)。
    """
    __tablename__ = "task_spec_doc"

    id = Column(BigInteger, primary_key=True, index=True, comment="文档ID")
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True, comment="任务ID")
    content = Column(Text().with_variant(LONGTEXT, "mysql"), nullable=False, default="",
                     comment="文档正文（markdown）")
    content_type = Column(String(16), nullable=False, default="markdown",
                          comment="正文格式，固定 markdown")
    source = Column(String(16), nullable=False, default="inline",
                    comment="来源：inline=在线编写 / upload=上传解析 / ai_summary=AI 生成")
    source_files = Column(JSON, nullable=True,
                          comment="原始上传文件引用：[{object_path, filename, size}]")
    created_by = Column(String(50), nullable=True, comment="创建者ID")
    updated_by = Column(String(50), nullable=True, comment="最近编辑者ID")
    revision = Column(Integer, nullable=False, server_default="1", default=1,
                      comment="修订号（乐观锁：保存时比对，不一致说明被他人改过）")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(),
                        nullable=False, comment="更新时间")

    __table_args__ = (
        UniqueConstraint("task_id", name="uq_task_spec_doc_task"),
    )

    def __repr__(self):
        return f"<TaskSpecDoc(id={self.id}, task_id={self.task_id}, revision={self.revision})>"


class RelationType(str, enum.Enum):
    """工单关联关系类型"""
    PREDECESSOR = "predecessor"   # 前置工单（阻塞：前置未完成则阻塞当前工单完成/关闭）
    DUPLICATE = "duplicate"       # 重复工单（仅标记，不阻塞）
    SUBTASK = "subtask"           # 子任务（父工单关闭时校验所有子任务完成）


class TaskRelation(Base):
    """工单关联表：工间的结构化关系。

    方向约定：
      - predecessor: source=当前工单, target=前置工单（target 需先完成）
      - duplicate:   source=当前工单, target=重复工单
      - subtask:     source=父工单, target=子工单

    约束：
      - (source_task_id, target_task_id, relation_type) 唯一
      - 禁止自引用（source == target）
      - predecessor 禁止成环（后端 DFS 校验）
      - subtask 同一 target 只能有一条（一个子任务只挂一个父工单）
    """
    __tablename__ = "task_relations"

    id = Column(BigInteger, primary_key=True, index=True, comment="关联ID")
    source_task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
                            nullable=False, index=True, comment="源工单ID")
    target_task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
                            nullable=False, index=True, comment="目标工单ID")
    relation_type: Mapped[RelationType] = mapped_column(SQLEnum(RelationType),
                                                        nullable=False, index=True, comment="关系类型")
    created_by = Column(String(50), nullable=True, index=True, comment="创建人username")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")

    __table_args__ = (
        UniqueConstraint("source_task_id", "target_task_id", "relation_type", name="uq_task_relation_unique"),
    )

    def __repr__(self):
        return f"<TaskRelation(id={self.id}, {self.source_task_id}->{self.target_task_id}, type={self.relation_type})>"


class SystemConfig(Base):
    """系统级配置键值对（工单关联规则等可在线配置项）。

    表名 system_config。key 全局唯一，value 统一存字符串（bool 用 "0"/"1"）。
    通过 task_policy_service 封装读写 + Redis 缓存热读。
    """
    __tablename__ = "system_config"

    id = Column(BigInteger, primary_key=True, index=True)
    config_key = Column(String(100), nullable=False, unique=True, index=True, comment="配置键")
    config_value = Column(String(500), nullable=False, comment="配置值（字符串，bool 用 0/1）")
    description = Column(String(255), nullable=True, comment="配置说明")
    # 注意：默认值必须用 CURRENT_TIMESTAMP，不能用 func.now()。
    # SQLAlchemy 检测到 MySQL >= 8.0.13 时会把 func.now() 渲染成带括号的表达式默认值
    # DEFAULT (now())，而 MySQL 8.0.13 对含表达式默认值的表执行 ALTER（如 CREATE INDEX）
    # 会报 1067 Invalid default value，导致 create_all 建表后建索引失败。
    updated_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(),
                        nullable=False, comment="更新时间")

    def __repr__(self):
        return f"<SystemConfig(key={self.config_key}, value={self.config_value})>"
