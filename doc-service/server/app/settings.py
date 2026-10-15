from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = Field(..., min_length=8)
    session_secret: str = Field(..., min_length=16)
    topo_content_key: str = Field(..., min_length=32)
    debug: bool = False
    openapi_enabled: bool = False
    bind_host: str = "127.0.0.1"
    bind_port: int = 8767
    session_cookie: str = "topo_session"
    session_hours: int = 12
    week_soft_cap: float = 40.0
    week_hard_cap: float = 40.0
    default_expected_hours: float = 24.0
    bootstrap_admin_password: str | None = None
    topo_public_url: str = ""
    topo_migrated_url: str = ""
    usp_test_base_url: str = "http://10.10.10.202:9200"
    usp_test_admin_license: str = ""

    @field_validator("debug", "openapi_enabled", mode="before")
    @classmethod
    def _flag(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return bool(v)

    @field_validator("usp_test_base_url", "usp_test_admin_license", "topo_migrated_url", "topo_public_url", mode="before")
    @classmethod
    def _strip(cls, v):
        return (v or "").strip().rstrip("/") if isinstance(v, str) else v


@lru_cache
def get_settings() -> Settings:
    if not os.environ.get("TOPO_CONTENT_KEY") and not os.environ.get("topo_content_key"):
        # pydantic-settings reads env case-insensitively; still fail closed if empty after load
        pass
    s = Settings()
    if not s.topo_content_key or s.topo_content_key.startswith("CHANGE"):
        raise RuntimeError("TOPO_CONTENT_KEY missing; refuse to start")
    if not s.session_secret or s.session_secret.startswith("CHANGE"):
        raise RuntimeError("SESSION_SECRET missing; refuse to start")
    return s
