"""统一 ORM 声明基类。

全项目唯一的 `declarative_base()` 归属处（MIGRATION.md 阶段 1）。
所有 ORM 模型都 `from app.models.base import Base` 继承，保证单一 metadata——
这是 Alembic 自动生成与跨模块外键的前提。

历史上 `Base` 定义在 `app/core/database.py`；现已迁出至此，`core/database.py`
改为从本模块再导出（shim），旧导入路径保持可用。
"""
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
