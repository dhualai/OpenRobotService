from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models.base import Base
from app.core.config import settings

DATABASE_URL = settings.DATABASE_URL or f"mysql+pymysql://root:123456@127.0.0.1:3306/helpdesk"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    echo=False
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 异步驱动：优先 asyncmy（C 扩展，更快）；Py3.14 等无预编译 wheel、编译失败的环境回退 aiomysql（纯 Python）
try:
    import asyncmy  # noqa: F401
    _ASYNC_DIALECT = "mysql+asyncmy"
except ModuleNotFoundError:
    import aiomysql  # noqa: F401
    _ASYNC_DIALECT = "mysql+aiomysql"
ASYNC_DATABASE_URL = DATABASE_URL.replace('mysql+pymysql', _ASYNC_DIALECT)
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    echo=False
)


def _ensure_utc_session(dbapi_connection, connection_record):
    """强制每个连接的会话时区为 UTC（SET time_zone = '+00:00'）。

    根因治理：MySQL 服务器系统时区在本地与生产不一致（本地 SYSTEM=UTC、生产 SYSTEM=+08:00），
    导致 ``func.now()`` 在本地返回 UTC、在生产返回 +8 时间。前端 ``parseUtcDate`` 对无时区
    ISO 字符串统一补 ``Z`` 当作 UTC 解析，因此在生产环境会被多加 8 小时。

    设置会话时区为 UTC 后，无论服务器系统时区为何，``func.now()`` 一律返回 UTC 时间，写入与读取
    语义在本地/生产完全一致；前端补 ``Z`` 的解析逻辑无需改动，跨环境时间显示正确。
    """
    cur = dbapi_connection.cursor()
    try:
        cur.execute("SET time_zone = '+00:00'")
    finally:
        cur.close()


event.listens_for(engine, "connect")(_ensure_utc_session)
event.listens_for(async_engine.sync_engine, "connect")(_ensure_utc_session)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False
)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()