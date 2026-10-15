from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings


def _engine():
    s = get_settings()
    return create_engine(s.database_url, pool_pre_ping=True)


engine = None
SessionLocal = None


def configure(url: str | None = None) -> None:
    global engine, SessionLocal
    db_url = url or get_settings().database_url
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        configure()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
