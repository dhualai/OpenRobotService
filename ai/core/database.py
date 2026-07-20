"""AI 模块独立数据库连接（不依赖 backend）

读取 DATABASE_URL 的方式：
  1. 环境变量 DATABASE_URL
  2. 从 backend/app/core/.env 读取
  3. 默认值
"""
import os
from pathlib import Path
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, JSON
from sqlalchemy.orm import sessionmaker, declarative_base


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
    assignee_id = Column(Integer, default=0)
    planned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
