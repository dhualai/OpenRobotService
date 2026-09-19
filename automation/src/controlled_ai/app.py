"""API-only controlled AI app used exclusively by automation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI

from .config import ControlledAIConfig


def _prepare_runtime_environment() -> Path:
    """Load the same AI/backend environment used by the regular AI service."""

    project_root = Path(__file__).resolve().parents[3]
    backend_dir = project_root / "backend"
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    load_dotenv(project_root / "ai" / ".env")
    backend_env = backend_dir / ".env"
    load_dotenv(backend_env)
    if backend_env.is_file():
        values = dotenv_values(backend_env)
        secret_key = values.get("SECRET_KEY") or values.get("JWT_SECRET")
        if secret_key:
            os.environ["SECRET_KEY"] = secret_key
        if values.get("ALGORITHM"):
            os.environ["ALGORITHM"] = values["ALGORITHM"]
    os.environ.setdefault("SECRET_KEY", "change-me-to-a-random-long-secret")
    os.environ.setdefault("ALGORITHM", "HS256")
    return project_root


def _configure_ai_environment(config: ControlledAIConfig) -> None:
    """Route real AI pipeline model calls to the local replay server."""

    defaults = {
        "LLM_BACKEND": "relay",
        "RELAY_BASE_URL": config.llm_base_url,
        "RELAY_API_KEY": "automation",
        "RELAY_MODEL": "automation-fixed",
        "RELAY_FALLBACK_MODELS": "",
        "INTENT_LLM_BACKEND": "relay",
        "INTENT_MODEL": "automation-fixed",
        "AI_TICKET_TOOL_LOOP": "0",
        "AI_DIAGNOSIS_TOOL_LOOP": "0",
        "AI_PLAN_EXECUTE": "0",
        "BACKEND_BASE_URL": config.backend_base_url,
        "AUTOMATION_CONTROLLED_REPLY": "1",
    }
    for key, value in defaults.items():
        os.environ[key] = value


def build_app(config: ControlledAIConfig | None = None) -> FastAPI:
    """Build the AI API without importing ``ai.run`` or its lifespan workers."""

    _prepare_runtime_environment()
    config = config or ControlledAIConfig.from_env()
    _configure_ai_environment(config)

    from ai.api import memory_router, qa_router

    app = FastAPI(title="OpenRobotService Automation AI")
    app.include_router(qa_router)
    app.include_router(memory_router)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "controlled-ai-api",
            "mode": "api-only",
            "background_workers": 0,
            "llm_backend": os.getenv("LLM_BACKEND"),
            "relay_base_url": os.getenv("RELAY_BASE_URL"),
            "relay_model": os.getenv("RELAY_MODEL"),
            "intent_llm_backend": os.getenv("INTENT_LLM_BACKEND"),
            "intent_model": os.getenv("INTENT_MODEL"),
            "plan_execute": os.getenv("AI_PLAN_EXECUTE"),
            "ticket_tool_loop": os.getenv("AI_TICKET_TOOL_LOOP"),
        }

    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    _config = ControlledAIConfig.from_env()
    uvicorn.run(
        "automation.src.controlled_ai.app:app",
        host=_config.host,
        port=_config.api_port,
    )
