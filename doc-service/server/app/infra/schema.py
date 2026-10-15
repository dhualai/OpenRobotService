from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.engine import Engine

from app.infra.models import Base

SERVER_ROOT = Path(__file__).resolve().parents[2]


def apply_schema(engine: Engine) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    cfg = Config(str(SERVER_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_ROOT / "alembic"))
    url = os.environ.get("DATABASE_URL") or engine.url.render_as_string(hide_password=False)
    cfg.set_main_option("sqlalchemy.url", url)

    if inspect(engine).has_table("users") and not inspect(engine).has_table("alembic_version"):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0001_initial')"))
    command.upgrade(cfg, "head")


def create_all(engine: Engine) -> None:
    Base.metadata.create_all(engine)
