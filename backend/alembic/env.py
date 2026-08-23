"""Alembic 运行环境（MIGRATION.md 阶段 1）。

要点：
- `target_metadata` 指向 `app.models.Base.metadata`——导入 `app.models` 即触发全部
  19 张表注册，autogenerate 以此为准。
- 数据库 URL 从 `app.core.config.settings.DB_CONFIG` 拼接，与运行时
  `app/core/database.py` 完全一致，杜绝双份数据源配置漂移。
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 导入统一模型包，使所有表注册到单一 metadata
from app.models import Base
from app.core.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """与 core/database.py 相同的拼接逻辑。"""
    db = settings.DB_CONFIG
    return (
        f"mysql+pymysql://{db['user']}:{db['password']}"
        f"@{db['host']}:{db['port']}/{db['database']}"
    )


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
