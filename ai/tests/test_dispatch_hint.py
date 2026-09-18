"""dispatch_hint（提单信息充分性信号）链路测试

覆盖：
  1. dispatch_hint_text 枚举话术映射（合法值/垃圾值/空）
  2. 三个派单 prompt 注入（部门判定 / L1 召回 / Step6 决策）：
     有值注入一行、无值不出现「提单提示」
  3. _build_ticket 生成端：LLM 输出合法枚举 → result 写入；
     垃圾值/缺字段 → result 不写键（白名单防幻觉）
"""
import json
from unittest.mock import AsyncMock

import pytest

from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    EngineerProfile, TicketContext, dispatch_hint_text,
)


# ================================================================
# 1. 话术映射
# ================================================================

class TestDispatchHintText:
    @pytest.mark.parametrize("hint,needle", [
        ("lacking", "信息不足"),
        ("severe", "严重不足"),
        (" lacking ", "信息不足"),  # 容忍首尾空白
    ])
    def test_known_levels_map_to_text(self, hint, needle):
        text = dispatch_hint_text(hint)
        assert text.startswith("提单提示：")
        assert needle in text

    @pytest.mark.parametrize("hint", [None, "", "whatever", "不足", "SEVERE"])
    def test_unknown_or_empty_returns_blank(self, hint):
        assert dispatch_hint_text(hint) == ""


def _ticket(**kw) -> TicketContext:
    base = dict(id="T1", title="测试工单", problem_description="机器人不动了",
                status="new")
    base.update(kw)
    return TicketContext(**base)


# ================================================================
# 2. 派单侧三个 prompt 注入
# ================================================================

_ENGS = [EngineerProfile(id="u1", name="张三")]


def _dept_prompt(ticket) -> str:
    from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.llm_dept_signal import LlmDeptSignal
    return LlmDeptSignal()._build_prompt(ticket)


def _recall_prompt(ticket) -> str:
    from ai.agents.AiDiagnosisPlatform.assigner.recall.llm_recall import LlmRecall
    return LlmRecall()._build_prompt(ticket, _ENGS, top_k=5)


def _decision_prompt(ticket) -> str:
    from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
    ranked = {"u1": {"total_score": 0.9, "llm_score": 0.9,
                     "semantic_score": 0.5, "history_score": 0.3}}
    return LlmDecision()._build_prompt(ticket, _ENGS, {}, ranked)


_ALL_PROMPTS = [_dept_prompt, _recall_prompt, _decision_prompt]


class TestPromptInjection:
    @pytest.mark.parametrize("build", _ALL_PROMPTS)
    @pytest.mark.parametrize("hint,needle", [
        ("lacking", "信息不足"),
        ("severe", "严重不足"),
    ])
    def test_hint_injected_when_present(self, build, hint, needle):
        prompt = build(_ticket(dispatch_hint=hint))
        assert "提单提示" in prompt
        assert needle in prompt

    @pytest.mark.parametrize("build", _ALL_PROMPTS)
    def test_no_hint_line_when_absent_or_garbage(self, build):
        for ticket in (_ticket(), _ticket(dispatch_hint=None),
                       _ticket(dispatch_hint="信息不太够")):
            assert "提单提示" not in build(ticket)


# ================================================================
# 3. 提单端生成（_build_ticket 白名单写入）
# ================================================================

def _llm_json(**extra) -> str:
    payload = {
        "title": "激光传感器故障", "type": "problem", "priority": "中",
        "description": "传感器无数据，需排查。",
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class TestBuildTicketHint:
    @pytest.mark.asyncio
    async def test_valid_hint_written_to_result(self, platform, mock_llm, make_state):
        mock_llm.complete = AsyncMock(return_value=_llm_json(dispatch_hint="severe"))
        memory = await platform._memory_manager.get_memory("dh-1")
        result = await platform._build_ticket("dh-1", make_state(), memory)
        assert result.get("dispatch_hint") == "severe"

    @pytest.mark.asyncio
    async def test_lacking_level_written(self, platform, mock_llm, make_state):
        mock_llm.complete = AsyncMock(return_value=_llm_json(dispatch_hint="lacking"))
        memory = await platform._memory_manager.get_memory("dh-2")
        result = await platform._build_ticket("dh-2", make_state(), memory)
        assert result.get("dispatch_hint") == "lacking"

    @pytest.mark.asyncio
    async def test_garbage_hint_dropped(self, platform, mock_llm, make_state):
        mock_llm.complete = AsyncMock(
            return_value=_llm_json(dispatch_hint="用户没说清楚"))
        memory = await platform._memory_manager.get_memory("dh-3")
        result = await platform._build_ticket("dh-3", make_state(), memory)
        assert "dispatch_hint" not in result

    @pytest.mark.asyncio
    async def test_absent_hint_no_key(self, platform, mock_llm, make_state):
        mock_llm.complete = AsyncMock(return_value=_llm_json())
        memory = await platform._memory_manager.get_memory("dh-4")
        result = await platform._build_ticket("dh-4", make_state(), memory)
        assert "dispatch_hint" not in result
