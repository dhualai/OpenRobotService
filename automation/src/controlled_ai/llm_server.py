"""OpenAI-compatible HTTP server backed by deterministic replay."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from .config import DEFAULT_REPLAY_FILE
from .replay import ReplayEngine, ReplayMismatchError


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_chunks(content: str, chunk_size: int = 64) -> Iterator[str]:
    for index in range(0, len(content), chunk_size):
        yield _sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": content[index:index + chunk_size]},
                        "finish_reason": None,
                    }
                ]
            }
        )
    yield _sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    yield "data: [DONE]\n\n"


def create_llm_app(engine: ReplayEngine) -> FastAPI:
    app = FastAPI(title="OpenRobotService Controlled LLM Replay")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "controlled-llm-replay",
            "case_id": engine.case_id,
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict[str, Any]):
        try:
            result = engine.reply(payload.get("messages") or [])
        except ReplayMismatchError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": f"controlled replay mismatch: {exc}"},
            )

        if payload.get("stream"):
            return StreamingResponse(
                _stream_chunks(result.content),
                media_type="text/event-stream",
            )

        return {
            "id": f"chatcmpl-controlled-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model") or "automation-fixed",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @app.post("/v1/responses")
    async def responses(payload: dict[str, Any]):
        try:
            result = engine.reply(payload.get("input") or [])
        except ReplayMismatchError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": f"controlled replay mismatch: {exc}"},
            )

        if payload.get("stream"):
            def event_stream() -> Iterator[str]:
                yield _sse(
                    {
                        "type": "response.output_text.delta",
                        "delta": result.content,
                    }
                )
                yield _sse({"type": "response.completed"})
                yield "data: [DONE]\n\n"

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        return {
            "id": f"resp-controlled-{int(time.time() * 1000)}",
            "object": "response",
            "status": "completed",
            "model": payload.get("model") or "automation-fixed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": result.content}
                    ],
                }
            ],
        }

    return app


app = create_llm_app(ReplayEngine(DEFAULT_REPLAY_FILE))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "automation.src.controlled_ai.llm_server:app",
        host="127.0.0.1",
        port=9410,
    )
