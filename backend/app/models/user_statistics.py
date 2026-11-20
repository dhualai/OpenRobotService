"""用户统计 ORM 模型。

表 `user_statistics`：按统计日期（ref_date）+ 用户来源（user_source）记录
每日新增用户（new_user）与取消关注用户（cancel_user）数量。
"""
from sqlalchemy import Column, Integer, Date

from app.models.base import Base


class UserStatistics(Base):
    __tablename__ = "user_statistics"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    # 统计日期，仅存年月日
    ref_date = Column(Date, nullable=False, index=True, comment="统计日期（仅年月日）")
    # 用户来源
    user_source = Column(Integer, nullable=False, comment="用户来源")
    # 新增用户数
    new_user = Column(Integer, nullable=False, default=0, comment="新增用户数")
    # 取消关注用户数
    cancel_user = Column(Integer, nullable=False, default=0, comment="取消关注用户数")
