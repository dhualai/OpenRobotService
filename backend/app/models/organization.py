"""公司/部门主数据 ORM 模型。

独立于 users 表的主数据表，支持审核流程（pending → approved/rejected）。
users 表通过 company_id / department_id 外键关联，不再直接存名称字符串。
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Text, JSON
from sqlalchemy.sql import func

from app.models.base import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), unique=True, nullable=False, comment="公司名称")
    status = Column(String(16), nullable=False, default="pending",
                    comment="审核状态：pending/approved/rejected")
    # 审计字段用普通字符串存储（不建立外键），避免与 users 表形成循环依赖
    created_by = Column(String(64), nullable=True, comment="提交者用户ID")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    approved_by = Column(String(64), nullable=True, comment="审核人用户ID")
    approved_at = Column(DateTime, nullable=True)
    reject_reason = Column(String(255), nullable=True, comment="驳回原因")


class Department(Base):
    __tablename__ = "departments"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, comment="部门名称")
    company_id = Column(String(64), ForeignKey("companies.id"), nullable=True,
                        comment="所属公司ID（nullable 用于历史无公司数据）")
    status = Column(String(16), nullable=False, default="pending",
                    comment="审核状态：pending/approved/rejected")
    # 部门职责画像（供 AI 派单 R2 部门分类）：职责描述 + 典型示例
    profile_text = Column(Text, nullable=True, comment="部门职责描述（AI 派单部门分类用）")
    examples = Column(JSON, nullable=True, comment="典型工单示例（[{title, dept}]）")
    # 审计字段用普通字符串存储（不建立外键），避免与 users 表形成循环依赖
    created_by = Column(String(64), nullable=True, comment="提交者用户ID")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    approved_by = Column(String(64), nullable=True, comment="审核人用户ID")
    approved_at = Column(DateTime, nullable=True)
    reject_reason = Column(String(255), nullable=True, comment="驳回原因")

    __table_args__ = (
        UniqueConstraint("name", "company_id", name="uq_department_name_company"),
    )
