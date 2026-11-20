#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""co 数据接入服务的独立数据库连接。

仅依赖 sqlalchemy / pymysql；不引用 backend/ 或 ai/ 目录下的任何代码，
与 data_access_service.py 同目录，保证 co 服务可独立部署运行。

DATABASE_URL 读取顺序：
  1. 环境变量 DATABASE_URL
  2. 项目根 backend/.env 中的 DATABASE_URL
  3. 默认值 mysql+pymysql://root:123456@127.0.0.1:3306/helpdesk
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, Column, String
from sqlalchemy.orm import sessionmaker, declarative_base


# co/db.py → parent=co → parent=OpenRobotService（项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    backend_env = _PROJECT_ROOT / "backend" / ".env"
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


class Project(Base):
    """交付项目表（仅查询）。

    字段对齐 backend/app/models/delivery.py 的 Project；这里只声明数据接入服务
    实际用到的列。MQTT 项目标识映射：
      key   = system_id（MQTT topic 中的 project 段，如 AJNQ）
      value = id（系统内项目编号，如 24；与 code 一致）
    """
    __tablename__ = "project"

    id = Column(String(64), primary_key=True, comment="项目ID/代码，与code一致")
    code = Column(String(64), unique=True, nullable=False, comment="项目代码")
    name = Column(String(128), nullable=False, comment="项目名称")
    system_id = Column(String(50), nullable=True, comment="系统ID（MQTT/外部系统标识）")
