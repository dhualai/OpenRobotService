from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models.base import Base
from app.core.config import settings

DATABASE_URL = settings.DATABASE_URL or f"mysql+pymysql://root:123456@127.0.0.1:3306/helpdesk"

engine = create_engine(DATABASE_URL)
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