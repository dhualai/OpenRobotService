"""Configuration for local UI regression runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class UiRegressionConfig:
    """Environment-backed local Gateway and SSH settings."""

    host: str = "127.0.0.1"
    port: int = 4173
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    backend_url: str = "http://127.0.0.1:19400"
    ai_url: str = "http://127.0.0.1:19411"
    upstream_timeout: float = 60.0
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_port: int = 22
    ssh_key: str = ""
    backend_remote_port: int = 9400
    backend_local_port: int = 19400
    ai_remote_port: int = 9411
    ai_local_port: int = 19411
    tunnel_timeout: float = 20.0

    @classmethod
    def from_env(cls) -> "UiRegressionConfig":
        return cls(
            host=os.getenv("UI_REGRESSION_HOST", "127.0.0.1"),
            port=int(os.getenv("UI_REGRESSION_PORT", "4173")),
            frontend_dist=Path(
                os.getenv(
                    "UI_REGRESSION_FRONTEND_DIST",
                    str(PROJECT_ROOT / "frontend" / "dist"),
                )
            ),
            backend_url=os.getenv(
                "UI_REGRESSION_BACKEND_URL",
                "http://127.0.0.1:19400",
            ).rstrip("/"),
            ai_url=os.getenv(
                "UI_REGRESSION_AI_URL",
                "http://127.0.0.1:19411",
            ).rstrip("/"),
            upstream_timeout=float(
                os.getenv("UI_REGRESSION_UPSTREAM_TIMEOUT", "60")
            ),
            ssh_host=os.getenv("UI_REGRESSION_SSH_HOST", ""),
            ssh_user=os.getenv("UI_REGRESSION_SSH_USER", ""),
            ssh_port=int(os.getenv("UI_REGRESSION_SSH_PORT", "22")),
            ssh_key=os.getenv("UI_REGRESSION_SSH_KEY", ""),
            backend_remote_port=int(
                os.getenv("UI_REGRESSION_BACKEND_REMOTE_PORT", "9400")
            ),
            backend_local_port=int(
                os.getenv("UI_REGRESSION_BACKEND_LOCAL_PORT", "19400")
            ),
            ai_remote_port=int(
                os.getenv("UI_REGRESSION_AI_REMOTE_PORT", "9411")
            ),
            ai_local_port=int(
                os.getenv("UI_REGRESSION_AI_LOCAL_PORT", "19411")
            ),
            tunnel_timeout=float(
                os.getenv("UI_REGRESSION_TUNNEL_TIMEOUT", "20")
            ),
        )
