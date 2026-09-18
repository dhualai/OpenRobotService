"""用户信息 ORM 模型。

表 `user_info`：以 JSON 形式存储用户信息，created_time 仅存年月日（DATE 类型）。
"""
from sqlalchemy import Column, Integer, JSON, Date, text

from app.models.base import Base


class UserInfo(Base):
    __tablename__ = "user_info"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    # 用户信息（JSON 格式）
    user_info = Column(JSON, nullable=False, comment="用户信息（JSON 格式）")
    # 创建日期，仅存年月日
    created_time = Column(Date, server_default=text("(CURRENT_DATE)"), nullable=False, comment="创建日期（仅年月日）")
