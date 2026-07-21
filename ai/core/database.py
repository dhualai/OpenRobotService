"""AI 模块独立数据库连接（不依赖 backend）

读取 DATABASE_URL 的方式：
  1. 环境变量 DATABASE_URL
  2. 从 backend/app/core/.env 读取
  3. 默认值
"""
import os
from pathlib import Path
from sqlalchemy import create_engine, Column, String, Integer, BigInteger, Text, DateTime, JSON, Enum as SQLEnum
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
    status = Column(String(20), default="pending", comment="pending/dispatched/in_progress/resolved/closed")
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

    id = Column(BigInteger, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    task_type = Column(String(30), nullable=False, default="problem")
    status = Column(String(30), nullable=False, default="new")
    priority = Column(String(30), nullable=False, default="medium")
    created_by = Column(String(50), nullable=False)
    assigned_to = Column(String(50), nullable=True)
    project_name = Column(String(255), nullable=True)
    project_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    source = Column(String(32), nullable=False, default="manual")


class ProjectDelivery(Base):
    """交付项目表（仅查询，字段对齐 backend/app/models/delivery.py Project）"""
    __tablename__ = "project"

    id = Column(String(64), primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    description = Column(String(1000), nullable=True)
    contact_person = Column(String(50), nullable=True)
    issues = Column(Integer, nullable=False, default=0)
    risks = Column(Integer, nullable=False, default=0)
    deployment_date = Column(String(20), nullable=True)
    deployment_version = Column(String(50), nullable=True)
    recent_delivery_date = Column(String(20), nullable=True)
    final_delivery_date = Column(String(20), nullable=True)
    project_summary = Column(String(1000), nullable=True)
    category_basis = Column(String(20), nullable=False, default="重要紧急")


class Risk(Base):
    """风险表（仅查询，字段对齐 backend/app/models/delivery.py Risk）"""
    __tablename__ = "risk"

    id = Column(Integer, primary_key=True, autoincrement=True)
    risk_code = Column(String(50), nullable=False)
    project_code = Column(String(50), nullable=False)
    project_name = Column(String(100), nullable=False)
    risk_category = Column(String(50), nullable=False)
    description = Column(String(1000), nullable=False)
    risk_level = Column(String(20), nullable=False)
    response_measure = Column(String(1000), nullable=True)
    progress = Column(String(100), nullable=True)
    responsible_person = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    discovery_time = Column(String(20), nullable=False)
    close_time = Column(String(30), nullable=True)
    created_at = Column(String(30), nullable=False)
    updated_at = Column(String(30), nullable=False)
