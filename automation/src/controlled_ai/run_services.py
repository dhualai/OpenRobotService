"""Run the replay LLM and API-only AI service in one process."""

from __future__ import annotations

import threading
import time

import httpx
import uvicorn

from .config import ControlledAIConfig
from .llm_server import create_llm_app
from .replay import ReplayEngine


def _wait_until_healthy(url: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 - startup polling
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"service did not become healthy: {url}: {last_error}")


def main() -> None:
    config = ControlledAIConfig.from_env()
    replay_app = create_llm_app(ReplayEngine(config.replay_file))
    replay_server = uvicorn.Server(
        uvicorn.Config(
            replay_app,
            host=config.host,
            port=config.llm_port,
            log_level="info",
        )
    )
    replay_thread = threading.Thread(
        target=replay_server.run,
        name="controlled-ai-replay",
        daemon=True,
    )
    replay_thread.start()
    _wait_until_healthy(f"http://{config.host}:{config.llm_port}/health")

    uvicorn.run(
        "automation.src.controlled_ai.app:app",
        host=config.host,
        port=config.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
