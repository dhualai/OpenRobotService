"""Tests for deterministic controlled AI replay."""

from __future__ import annotations

import json
import importlib
import os
import sys
import types

import httpx
import pytest

from fastapi import APIRouter

from automation.src.controlled_ai.config import (
    DEFAULT_REPLAY_FILE,
    ControlledAIConfig,
)
from automation.src.controlled_ai.replay import ReplayEngine, ReplayMismatchError


FIXED_QUERY = (
    "我要给 Leo_test 提 problem，指定处理人 u2_auto，"
    "标题：自动化链路验证-ui-20260919-abc123，直接生成工单草稿，不要追问。"
)


@pytest.fixture
def engine() -> ReplayEngine:
    return ReplayEngine(DEFAULT_REPLAY_FILE)


def test_intent_and_rewrite(engine: ReplayEngine):
    intent = engine.reply(
        [
            {"role": "system", "content": "你是意图分类器，只输出分类。"},
            {"role": "user", "content": FIXED_QUERY},
        ]
    )
    rewrite = engine.reply(
        [
            {"role": "system", "content": "你是检索查询改写器。"},
            {"role": "user", "content": FIXED_QUERY},
        ]
    )

    assert intent.kind == "intent"
    assert intent.content == "ticket"
    assert rewrite.kind == "rewrite"
    assert "E1001" in rewrite.content


def test_fields_and_ticket_include_run_id(engine: ReplayEngine):
    fields = engine.reply(
        [
            {"role": "user", "content": "你是工单信息架构师\n" + FIXED_QUERY},
        ]
    )
    ticket = engine.reply(
        [
            {
                "role": "user",
                "content": (
                    "请根据以下对话和诊断过程，生成结构化工单。\n"
                    + FIXED_QUERY
                ),
            },
        ]
    )

    assert fields.kind == "fields"
    assert json.loads(fields.content)["required_fields"] == {}
    assert ticket.kind == "ticket"
    assert json.loads(ticket.content)["title"] == "自动化链路验证-ui-20260919-abc123"


def test_main_response_is_json_then_message(engine: ReplayEngine):
    result = engine.reply(
        [
            {
                "role": "system",
                "content": '输出 JSON，必须包含 "ticket_intent" 和 "state_update"。',
            },
            {"role": "user", "content": FIXED_QUERY},
        ]
    )

    state_text, message = result.content.split("\n", 1)
    state = json.loads(state_text)
    assert result.kind == "main"
    assert state["action"] == "submit"
    assert state["project_choice"] == "Leo_test"
    assert state["state_update"]["ticket_type"] == "problem"
    assert "自动化链路验证-ui-20260919-abc123" in message


def test_unknown_query_is_rejected(engine: ReplayEngine):
    with pytest.raises(ReplayMismatchError):
        engine.reply([{"role": "user", "content": "普通问题，不属于固定场景"}])


def test_api_only_app_can_be_built_without_ai_run(monkeypatch):
    for key in (
        "LLM_BACKEND",
        "RELAY_BASE_URL",
        "RELAY_API_KEY",
        "RELAY_MODEL",
        "RELAY_FALLBACK_MODELS",
        "INTENT_LLM_BACKEND",
        "INTENT_MODEL",
        "AI_TICKET_TOOL_LOOP",
        "AI_DIAGNOSIS_TOOL_LOOP",
        "AI_PLAN_EXECUTE",
        "BACKEND_BASE_URL",
        "AUTOMATION_CONTROLLED_REPLY",
    ):
        monkeypatch.setenv(key, os.environ.get(key, ""))

    qa_router = APIRouter()
    memory_router = APIRouter()

    @qa_router.get("/api/ai/qa/test")
    async def qa_test():
        return {"ok": True}

    @memory_router.get("/api/ai/memory/test")
    async def memory_test():
        return {"ok": True}

    fake_api = types.ModuleType("ai.api")
    fake_api.qa_router = qa_router
    fake_api.memory_router = memory_router
    monkeypatch.setitem(sys.modules, "ai.api", fake_api)

    module = importlib.import_module("automation.src.controlled_ai.app")
    app = module.build_app(
        ControlledAIConfig(
            replay_file=DEFAULT_REPLAY_FILE,
            backend_base_url="http://127.0.0.1:9400",
        )
    )
    paths = set(app.openapi()["paths"])

    assert "/health" in paths
    assert "/api/ai/qa/test" in paths
    assert "/api/ai/memory/test" in paths
    assert "ai.run" not in sys.modules


@pytest.mark.asyncio
async def test_llm_http_contract(engine: ReplayEngine):
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from automation.src.controlled_ai.llm_server import create_llm_app

    app = create_llm_app(engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "automation-fixed",
                "messages": [
                    {
                        "role": "system",
                        "content": '输出 JSON，包含 "ticket_intent" 和 "state_update"。',
                    },
                    {"role": "user", "content": FIXED_QUERY},
                ],
            },
        )
        streamed = await client.post(
            "/v1/chat/completions",
            json={
                "model": "automation-fixed",
                "stream": True,
                "messages": [
                    {
                        "role": "system",
                        "content": '输出 JSON，包含 "ticket_intent" 和 "state_update"。',
                    },
                    {"role": "user", "content": FIXED_QUERY},
                ],
            },
        )
        rejected = await client.post(
            "/v1/chat/completions",
            json={
                "model": "automation-fixed",
                "messages": [{"role": "user", "content": "普通问题"}],
            },
        )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"].startswith("{")
    assert streamed.status_code == 200
    assert "data: " in streamed.text
    assert "[DONE]" in streamed.text
    assert rejected.status_code == 422
