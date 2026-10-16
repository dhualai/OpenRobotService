"""公告 ORM 模型（活动中心：首页固定公告条 + 抽屉内公告全文）。

公告由后台「公告管理」维护（增删改查 + 上下线）：
  - status: draft(草稿) / published(已发布) / offline(已下线)
  - is_pinned: 置顶。首页公告条只常驻展示一条，置顶优先，其次按发布时间倒序
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, String, Text, text,
)
from sqlalchemy.sql import func

from app.models.base import Base


class AnnouncementStatus:
    """公告状态常量（String 列存值，与微信二维码 QrcodeStatus 同风格）。

    用字符串而非 DB Enum：避免枚举增删时的迁移成本（见仓库既有做法）。
    """
    DRAFT = "draft"
    PUBLISHED = "published"
    OFFLINE = "offline"


class Announcement(Base):
    """公告表。"""
    __tablename__ = "announcements"

    id = Column(BigInteger, primary_key=True, index=True, comment="公告ID")
    title = Column(String(200), nullable=False, comment="公告标题")
    content = Column(Text, nullable=False, comment="公告正文（纯文本，换行保留）")
    is_pinned = Column(Boolean, nullable=False, server_default=text("0"), index=True,
                       comment="是否置顶：首页公告条优先展示置顶公告")
    status = Column(String(16), nullable=False, server_default=text("'draft'"), index=True,
                    comment="状态：draft/published/offline")
    published_at = Column(DateTime, nullable=True, comment="发布时间（上线时写入，下线不清空）")
    created_by = Column(String(50), nullable=True, comment="创建人 username")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(),
                        nullable=False, comment="更新时间")

    def __repr__(self):
        return f"<Announcement(id={self.id}, title='{self.title}', status={self.status})>"
