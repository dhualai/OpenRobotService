"""ORS-985 项目闸门状态修复的单测。

两个生产事故形态：
  A. 985：连提多单答项目题序号「2」→ 规划联想上一单 AI 号 86636 当查单
     → 查单上下文带偏主 LLM 输出空 problem_summary → 无脑覆盖清空真实需求
     → 工单退化「用户仅发送数字2」。
  B. 1019：用户「我的项目不在里面」→ 项目题状态无出口 → prepare 反复
     重出同一道题 + 长题面把原始问题挤出上下文窗口。

修复面：①problem_summary 空值防降级 ②序号窗口跳规划 ③查单号原话溯源
④project_none 出口 + 重出题防抖。此处覆盖可直接单测的 ①②③ 与 ④的状态
字段；闸门/prepare 端到端走真实链路冒烟。
"""
import asyncio
import sys
import os
import pytest
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AgentState,
    AiDiagnosisPlatform,
    _parse_seq_reply,
    _resolve_seq_choice,
    _load_agent_state,
    _save_agent_state,
)


def _state(**kw):
    defaults = dict(session_id="test-985", phase="diagnosing",
                    problem_summary="", project_asked=False,
                    project_candidates=[], project_ask_user_replied=False,
                    project_via_dialog=False)
    defaults.update(kw)
    return AgentState(**defaults)


# ================================================================
# 修复①：problem_summary 空值防降级
# ================================================================

class TestSummaryNoDowngrade:
    def _apply(self, state, su):
        AiDiagnosisPlatform._apply_state_update(None, state, su)

    def test_empty_new_keeps_nonempty_old(self):
        """985 死因：空新值不得清掉已回填的真实需求。"""
        s = _state(problem_summary="四川华川无尘车间缓存区，人工拍呼叫器叫车搬货到缓存区")
        self._apply(s, {"problem_summary": ""})
        assert "四川华川" in s.problem_summary

    def test_whitespace_new_keeps_nonempty_old(self):
        s = _state(problem_summary="阈值配置问题")
        self._apply(s, {"problem_summary": "   "})
        assert s.problem_summary == "阈值配置问题"

    def test_nonempty_new_still_overwrites(self):
        """正常覆盖行为不回归（新信息替换旧摘要）。"""
        s = _state(problem_summary="旧问题描述")
        self._apply(s, {"problem_summary": "新的问题描述"})
        assert s.problem_summary == "新的问题描述"

    def test_empty_new_on_empty_old_stays_empty(self):
        s = _state(problem_summary="")
        self._apply(s, {"problem_summary": ""})
        assert s.problem_summary == ""

    def test_resolved_guard_unchanged(self):
        """resolved 闭环拦截不回归：仍禁止 LLM 设置。"""
        s = _state(phase="resolved", problem_summary="")
        self._apply(s, {"problem_summary": "从旧对话捞出来的问题"})
        assert s.problem_summary == ""


# ================================================================
# 修复②：序号窗口跳规划 —— 依赖的解析面回归
# ================================================================

class TestSeqReplyParsing:
    def test_pure_digits(self):
        assert _parse_seq_reply("2") == 2
        assert _parse_seq_reply(" 3 ") == 3

    def test_ordinal_forms(self):
        assert _parse_seq_reply("第2个") == 2
        assert _parse_seq_reply("3号") == 3
        assert _parse_seq_reply("一") == 1

    def test_non_seq_messages(self):
        """985/1019 场景里的非序号消息不得误判为序号（防抖依赖）。"""
        assert _parse_seq_reply("不在里面") is None
        assert _parse_seq_reply("不在里面，转工单") is None
        assert _parse_seq_reply("我的项目不在里面") is None
        assert _parse_seq_reply("转工单") is None


class TestResolveSeqChoice:
    CANDS = [{"name": "A项目", "code": "1"}, {"name": "B项目", "code": "2"}]

    def test_valid_seq(self):
        handled, c = _resolve_seq_choice("2", self.CANDS)
        assert handled and c["name"] == "B项目"

    def test_out_of_range(self):
        handled, c = _resolve_seq_choice("7", self.CANDS)
        assert handled and c is None

    def test_not_seq(self):
        handled, c = _resolve_seq_choice("不在里面", self.CANDS)
        assert not handled


# ================================================================
# 修复③：查单号原话溯源门
# ================================================================

class TestLookupTicketSourceGate:
    @pytest.fixture(autouse=True)
    def _patch_lookup(self, monkeypatch):
        """_execute_plan_tools 内部直调模块级 _lookup_ticket_ref（不走
        self），必须 patch 模块属性才拦得住。"""
        import ai.agents.AiDiagnosisPlatform.pipeline as pl
        self._mock = AsyncMock(return_value="#86636 工单内容")
        monkeypatch.setattr(pl, "_lookup_ticket_ref", self._mock)

    def _pipeline(self):
        return object.__new__(AiDiagnosisPlatform)

    def test_number_in_query_executes(self):
        """用户原话报了号（正常查单形态）→ 放行执行。"""
        p = self._pipeline()
        state = _state()
        out = asyncio.run(p._execute_plan_tools(
            "s", state, [("lookup_ticket", {"ticket_no": "983"})],
            created_by="u", query="帮我查下983号工单"))
        self._mock.assert_awaited_once_with("983", "u")
        assert "工单内容" in out

    def test_number_not_in_query_rejected(self):
        """985 形态：号来自上下文联想（原话只有「2」）→ 拒查不挂上下文。"""
        p = self._pipeline()
        state = _state()
        out = asyncio.run(p._execute_plan_tools(
            "s", state, [("lookup_ticket", {"ticket_no": "86636"})],
            created_by="u", query="2"))
        self._mock.assert_not_awaited()
        assert out == ""
        assert state.ticket_ref_context == ""

    def test_empty_query_keeps_compat(self):
        """query 未传（历史调用点）→ 不校验，行为兼容。"""
        p = self._pipeline()
        state = _state()
        asyncio.run(p._execute_plan_tools(
            "s", state, [("lookup_ticket", {"ticket_no": "983"})],
            created_by="u"))
        self._mock.assert_awaited_once()


# ================================================================
# 修复④：出口/防抖 state 字段（序列化往返不丢）
# ================================================================

class TestGateStateFields:
    class _Mem:
        def __init__(self):
            self.metadata = {}

    def test_serialize_roundtrip(self):
        s = _state(project_asked=True,
                   project_ask_user_replied=True,
                   project_via_dialog=True)
        m = self._Mem()
        _save_agent_state(m, s)
        s2 = _load_agent_state(m.metadata)
        assert s2.project_ask_user_replied is True
        assert s2.project_via_dialog is True

    def test_defaults_for_legacy_state(self):
        """存量会话的 state 无新字段 → 反序列化取默认 False，不炸。"""
        m = self._Mem()
        m.metadata["agent_state"] = {"session_id": "legacy", "phase": "idle"}
        s2 = _load_agent_state(m.metadata)
        assert s2.project_ask_user_replied is False
        assert s2.project_via_dialog is False
