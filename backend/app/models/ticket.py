"""工单 ORM 模型"""
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, Enum, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    # ── 通用字段 ──
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True, comment="AI 会话 ID")
    ticket_ai_id: Mapped[str] = mapped_column(String(32), comment="AI 生成的 ticket_id")
    title: Mapped[str] = mapped_column(String(20), comment="标题")
    description: Mapped[str] = mapped_column(String(150), comment="问题描述")
    status: Mapped[str] = mapped_column(String(20), default="pending_dispatch", comment="待派单/已派单/处理中/已解决/已关闭")
    type: Mapped[str] = mapped_column(String(20), comment="problem/bug/feature/support/other")
    priority: Mapped[str] = mapped_column(String(10), default="中", comment="紧急/高/中/低")
    contact: Mapped[str] = mapped_column(String(50), default="", comment="现场联系人")
    # 以下字段关联其他表，当前存原始值
    project_id: Mapped[int] = mapped_column(Integer, default=0, comment="项目 ID")
    creator_id: Mapped[int] = mapped_column(Integer, default=0, comment="发起人 ID")
    assignee_id: Mapped[int] = mapped_column(Integer, default=0, comment="接收人 ID")
    planned_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, comment="计划完成时间")
    attachments: Mapped[str] = mapped_column(JSON, default=list, comment="附件列表")

    # ── 诊断信息 ──
    diagnosis: Mapped[str] = mapped_column(JSON, default=dict, comment="Agent 诊断链")

    # ── 报障专属 (type=problem) ──
    location: Mapped[str] = mapped_column(String(200), default="", comment="现场位置")
    robot_type: Mapped[str] = mapped_column(String(50), default="", comment="机器人类型/编号")
    fault_code: Mapped[str] = mapped_column(String(100), default="", comment="故障码")
    special_notes: Mapped[str] = mapped_column(Text, default="", comment="特殊说明")

    # ── 缺陷专属 (type=bug) ──
    steps_to_reproduce: Mapped[str] = mapped_column(Text, default="", comment="复现步骤")
    expected_result: Mapped[str] = mapped_column(String(150), default="")
    actual_result: Mapped[str] = mapped_column(String(150), default="")
    severity: Mapped[str] = mapped_column(String(10), default="", comment="阻塞/主要/次要/轻微")
    version: Mapped[str] = mapped_column(String(30), default="", comment="版本号")

    # ── 功能需求 (type=feature) ──
    scenario: Mapped[str] = mapped_column(Text, default="", comment="需求场景")
    expected_effect: Mapped[str] = mapped_column(String(150), default="")
    source: Mapped[str] = mapped_column(String(20), default="", comment="客户提出/内部发现/竞品对标")

    # ── 支持请求 (type=support) ──
    support_type: Mapped[str] = mapped_column(String(30), default="")
    preferred_response: Mapped[str] = mapped_column(String(10), default="", comment="电话/现场/线上")

    # ── 时间戳 ──
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
