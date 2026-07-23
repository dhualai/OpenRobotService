"""AI 模块独立数据库连接（不依赖 backend）

读取 DATABASE_URL 的方式：
  1. 环境变量 DATABASE_URL
  2. 从 backend/app/core/.env 读取
  3. 默认值
"""
import os
from pathlib import Path
from sqlalchemy import create_engine, Column, String, Integer, BigInteger, Text, DateTime, JSON, Index, UniqueConstraint, Enum as SQLEnum
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func


# ai/core/database.py → parent=core → parent=ai → parent=项目根
_project_root = Path(__file__).resolve().parent.parent.parent


def _get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    # 尝试从 backend/.env 读取
    backend_env = _project_root / "backend" / ".env"
    if backend_env.exists():
        for line in backend_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL") and "=" in line:
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                if url:
                    return url
    return "mysql+pymysql://root:123456@127.0.0.1:3306/helpdesk"


DATABASE_URL = _get_database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Ticket(Base):
    """工单表（仅查询，字段对齐 backend/app/models/ticket.py）"""
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(128), index=True)
    ticket_ai_id = Column(String(32))
    title = Column(String(20))
    description = Column(String(150))
    status = Column(String(20), default="pending_dispatch", comment="待派单/已派单/处理中/已解决/已关闭")
    type = Column(String(20))
    priority = Column(String(10), default="中")
    contact = Column(String(50), default="")
    location = Column(String(200), default="")
    robot_type = Column(String(50), default="")
    fault_code = Column(String(100), default="")
    special_notes = Column(Text, default="")
    steps_to_reproduce = Column(Text, default="")
    expected_result = Column(String(150), default="")
    actual_result = Column(String(150), default="")
    severity = Column(String(10), default="")
    version = Column(String(30), default="")
    scenario = Column(Text, default="")
    expected_effect = Column(String(150), default="")
    source = Column(String(20), default="")
    support_type = Column(String(30), default="")
    preferred_response = Column(String(10), default="")
    attachments = Column(JSON, default=list)
    diagnosis = Column(JSON, default=dict)
    project_id = Column(Integer, default=0)
    creator_id = Column(Integer, default=0)
    created_by = Column(String(64), default="", index=True)
    assignee_id = Column(Integer, default=0)
    planned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class Task(Base):
    """任务表（仅查询，字段对齐 backend/app/models/task.py）"""
    __tablename__ = "tasks"

    id = Column(BigInteger, primary_key=True, index=True, comment="任务ID")
    title = Column(String(255), nullable=False, index=True, comment="任务标题")
    description = Column(Text, nullable=False, comment="任务描述")
    task_type = Column(String(30), nullable=False, default="problem", index=True, comment="任务类型: problem/bug/feature/support/other")
    status = Column(String(30), nullable=False, default="new", index=True, comment="任务状态: new/in_progress/pending/resolved/canceled/closed")
    priority = Column(String(30), nullable=False, default="medium", index=True, comment="任务优先级: low/medium/high/urgent")
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
    canceled_at = Column(DateTime, nullable=True, comment="取消时间")
    closed_at = Column(DateTime, nullable=True, comment="关闭时间")
    deadline_at = Column(DateTime, nullable=True, comment="截止时间")
    tags = Column(JSON, nullable=True, comment="标签列表")
    metadata_info = Column(JSON, nullable=True, comment="扩展元数据")
    attachments = Column(JSON, nullable=True, comment="附件列表")
    reply_count = Column(Integer, nullable=False, default=0, comment="回复数量")
    view_count = Column(Integer, nullable=False, default=0, comment="查看数量")
    source = Column(String(32), nullable=False, default="manual", index=True, comment="任务来源: manual/zentao/...")
    external_id = Column(String(64), nullable=True, index=True, comment="外部系统任务ID")
    external_url = Column(String(512), nullable=True, comment="外部系统跳转链接")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_task_source_external"),
    )


class ProjectDelivery(Base):
    """交付项目表（仅查询，字段对齐 backend/app/models/delivery.py Project）"""
    __tablename__ = "project"

    id = Column(String(64), primary_key=True, comment="项目ID/代码，与code一致")
    code = Column(String(64), unique=True, nullable=False, comment="项目代码")
    name = Column(String(128), nullable=False, comment="项目名称")
    system_id = Column(String(50), nullable=True, comment="系统ID")
    description = Column(String(1000), nullable=True, comment="项目描述")
    contact_person = Column(String(50), nullable=True, comment="对接人")
    contact_person_id = Column(String(20), nullable=True, comment="对接人ID")
    status = Column(String(20), nullable=False, default="active", comment="状态")
    expected_trend = Column(String(20), nullable=True, comment="预计走向")
    issues = Column(Integer, nullable=False, default=0, comment="问题数")
    risks = Column(Integer, nullable=False, default=0, comment="风险数")
    personnel_plan = Column(String(50), nullable=True, comment="人员计划")
    risk_list = Column(String(500), nullable=True, comment="风险清单")
    deployment_date = Column(String(20), nullable=True, comment="部署时间")
    deployment_version = Column(String(50), nullable=True, comment="部署版本")
    recent_delivery_date = Column(String(20), nullable=True, comment="近期交付时间")
    recent_delivery_content = Column(String(500), nullable=True, comment="近期交付内容")
    final_delivery_date = Column(String(20), nullable=True, comment="最终交付时间")
    project_summary = Column(String(1000), nullable=True, comment="项目总结")
    task_execution_status = Column(String(50), nullable=True, comment="任务执行情况")
    field_links = Column(String(1000), nullable=True, comment="字段链接(JSON格式)")
    category_basis = Column(String(20), nullable=False, default="重要紧急", comment="分类依据")

    __table_args__ = (
        Index("idx_project_code", "code", unique=True),
        Index("idx_project_status", "status"),
    )


class Risk(Base):
    """风险表（仅查询，字段对齐 backend/app/models/delivery.py Risk）"""
    __tablename__ = "risk"

    id = Column(Integer, primary_key=True, autoincrement=True)
    risk_code = Column(String(50), nullable=False, unique=True, comment="风险代码")
    project_code = Column(String(50), nullable=False, comment="项目代码")
    project_name = Column(String(100), nullable=False, comment="项目名称")
    risk_category = Column(String(50), nullable=False, comment="风险分类")
    custom_category = Column(String(50), nullable=True, comment="自定义分类")
    description = Column(String(1000), nullable=False, comment="风险描述")
    risk_level = Column(String(20), nullable=False, comment="风险等级")
    response_measure = Column(String(1000), nullable=True, comment="应对措施")
    progress = Column(String(100), nullable=True, comment="进度")
    responsible_person = Column(String(50), nullable=False, comment="负责人")
    responsible_person_id = Column(String(20), nullable=False, comment="负责人ID")
    status = Column(String(20), nullable=False, comment="状态")
    discovery_time = Column(String(20), nullable=False, comment="发现时间")
    close_time = Column(String(30), nullable=True, comment="关闭时间")
    created_at = Column(String(30), nullable=False, comment="创建时间")
    updated_at = Column(String(30), nullable=False, comment="更新时间")

    __table_args__ = (
        Index("idx_risk_project", "project_code", "project_name"),
        Index("idx_risk_status", "status"),
        Index("idx_risk_discovery_time", "discovery_time"),
    )


class Conversation(Base):
    """会话表（对齐 backend/app/models/conversation.py）"""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, default="新会话", comment="会话标题")
    user_id = Column(String(255), nullable=False, default="", comment="用户ID")
    scene_type = Column(String(255), nullable=False, default="chat", comment="场景类型: chat/faq/support/consultation/other")
    service_ticket_id = Column(String(255), nullable=False, default="", comment="关联工单ID")
    metadata_ = Column(Text, nullable=True, comment="元数据")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")


class Message(Base):
    """消息表（对齐 backend/app/models/conversation.py Message）"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, nullable=False, comment="会话ID")
    role = Column(String(20), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")
    message_type = Column(String(20), nullable=False, default="text", comment="消息类型: text/image/file/audio/multimodal")
    file_urls = Column(Text, nullable=True, comment="文件URL列表")
    parent_message_id = Column(Integer, nullable=True, comment="父消息ID")
    sequence = Column(Integer, nullable=False, default=0, comment="消息序号")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    metadata_ = Column(Text, nullable=True, comment="元数据")
