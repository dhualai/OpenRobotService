"""Configuration for the automation-only controlled AI service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_REPLAY_FILE = (
    Path(__file__).resolve().parents[2]
    / "testdata"
    / "controlled_ai"
    / "call_ticket_close.json"
)


@dataclass(frozen=True)
class ControlledAIConfig:
    """Environment-backed configuration for the controlled AI service."""

    host: str = "127.0.0.1"
    api_port: int = 9411
    llm_port: int = 9410
    replay_file: Path = DEFAULT_REPLAY_FILE
    backend_base_url: str = "http://127.0.0.1:9400"

    @classmethod
    def from_env(cls) -> "ControlledAIConfig":
        return cls(
            host=os.getenv("CONTROLLED_AI_HOST", "127.0.0.1"),
            api_port=int(os.getenv("CONTROLLED_AI_API_PORT", "9411")),
            llm_port=int(os.getenv("CONTROLLED_AI_LLM_PORT", "9410")),
            replay_file=Path(
                os.getenv(
                    "CONTROLLED_AI_REPLAY_FILE",
                    str(DEFAULT_REPLAY_FILE),
                )
            ),
            backend_base_url=os.getenv(
                "BACKEND_BASE_URL",
                "http://127.0.0.1:9400",
            ).rstrip("/"),
        )

    @property
    def llm_base_url(self) -> str:
        return f"http://{self.host}:{self.llm_port}/v1"

    @property
    def api_base_url(self) -> str:
        return f"http://{self.host}:{self.api_port}"
