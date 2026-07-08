from typing import Optional
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SQLEnum, BigInteger, Boolean, JSON, ForeignKey, desc
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, mapped_column, Mapped
import enum
from app.core.database import Base


class TicketStatus(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketType(str, enum.Enum):
    PROBLEM = "problem"
    FEATURE = "feature"
    BUG = "bug"
    SUPPORT = "support"
    OTHER = "other"


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(BigInteger, primary_key=True, index=True, comment="工单ID")
    title = Column(String(255), nullable=False, index=True, comment="工单标题")
    description = Column(Text, nullable=False, comment="工单描述")
    ticket_type: Mapped[TicketType] = mapped_column(SQLEnum(TicketType), nullable=False, default=TicketType.PROBLEM, index=True, comment="工单类型")
    
    status: Mapped[TicketStatus] = mapped_column(SQLEnum(TicketStatus), nullable=False, default=TicketStatus.NEW, index=True, comment="工单状态")
    priority: Mapped[TicketPriority] = mapped_column(SQLEnum(TicketPriority), nullable=False, default=TicketPriority.MEDIUM, index=True, comment="工单优先级")
    
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
        return f"<Ticket(id={self.id}, title='{self.title}', status={self.status})>"

    @property
    def is_open(self) -> bool:
        return self.status in [TicketStatus.NEW, TicketStatus.IN_PROGRESS, TicketStatus.PENDING]

    @property
    def is_resolved(self) -> bool:
        return self.status == TicketStatus.RESOLVED

    @property
    def is_closed(self) -> bool:
        return self.status == TicketStatus.CLOSED


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id = Column(BigInteger, primary_key=True, index=True, comment="评论ID")
    ticket_id = Column(BigInteger, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True, comment="工单ID")
    content = Column(Text, nullable=False, comment="评论内容")
    
    created_by = Column(String(50), nullable=False, index=True, comment="创建者ID")
    
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")
    
    is_public = Column(Boolean, nullable=False, default=True, comment="是否公开")
    attachments = Column(JSON, nullable=True, comment="附件列表")
    
    ticket = relationship("Ticket", backref="comments", order_by=lambda: desc(TicketComment.created_at))

    def __repr__(self):
        return f"<TicketComment(id={self.id}, ticket_id={self.ticket_id}, created_by='{self.created_by}')>"