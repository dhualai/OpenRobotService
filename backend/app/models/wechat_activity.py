"""微信用户日活 ORM 模型。

表 `wechat_user_activity`：按活跃日期（ref_date）+ 微信 openid 记录每个
用户当天的活跃情况，唯一约束 (ref_date, openid) 保证同一用户同一天只有
一行（日活 DAU 去重口径），重复活跃时刷新 last_active_at 与 source
（source 口径为「当日最后活跃来源」）。
"""
from sqlalchemy import Column, Integer, String, Date, DateTime, UniqueConstraint, text

from app.models.base import Base


class WechatUserActivity(Base):
    __tablename__ = "wechat_user_activity"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    # 活跃日期，仅存年月日
    ref_date = Column(Date, nullable=False, index=True, comment="活跃日期（仅年月日）")
    # 微信 openid（users.id 即 openid）
    openid = Column(String(128), nullable=False, index=True, comment="微信用户openid")
    # 当日最后活跃来源：message=公众号发消息，menu=点菜单，event=关注/扫码事件，h5=打开H5页
    source = Column(String(32), nullable=False, default="message", comment="当日最后活跃来源")
    # 当日最后活跃时间（重复活跃时刷新）
    # 默认值必须用 CURRENT_TIMESTAMP 而非 func.now()：MySQL 8.0.13+ 会把 func.now()
    # 渲染成 DEFAULT (now())，导致 create_all 建表后建索引报 1067 Invalid default value。
    last_active_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"), nullable=False, comment="当日最后活跃时间")

    __table_args__ = (
        UniqueConstraint("ref_date", "openid", name="uq_wua_ref_date_openid"),
    )
