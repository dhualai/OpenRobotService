"""DispatchFlow 端到端集成测试：覆盖 aassign 主流程关键路径。

全 mock，不连库/LLM/Qdrant/Redis。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_e2e_flow.py -v
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import (
    DeptRoutingResult,
    ProductRoutingResult,
    TightenResult,
)
from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult,
    EngineerProfile,
    TicketContext,
)


def _eng(eid="u1", name="张三", dept="智能规划研究院", job_level=1) -> EngineerProfile:
    return EngineerProfile(
        id=eid, name=name, department=dept, job_level=job_level,
        responsibility_modules={"调度USP": {"监控": ["路径规划"]}},
        duty_text="负责路径规划",
    )


def _ticket(**kw) -> TicketContext:
    base = dict(id="t-e2e", title="车辆故障", problem_description="车停了", status="new")
    base.update(kw)
    return TicketContext(**base)


def _tighten_result(candidates, before=None, after=None):
    return TightenResult(
        candidates=candidates,
        before_count=before or len(candidates),
        after_count=after or len(candidates),
        dept=DeptRoutingResult(mode="no_filter", primary_dept="", confidence=0.0),
        product=ProductRoutingResult(product="", source="test"),
    )


def _cfg(**kwargs):
    data = dict(
        job_level_penalty={1: 1.0, 2: 0.90, 3: 0.90},
        preferred_floor=0.9,
        llm_decision_topk=0,
        preferred_assignee_enabled=True,
        preferred_assignee_force_keep=True,
        department_routing={},
        product_routing={},
        history_recall={},
        vague_strong_signals={"enabled": True},
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


class TestStep0DirectAssign:
    """路径 A：Step0 强信号指定人 → 直接返回。"""

    def test_strong_signal_skips_pipeline(self):
        """正常流程：「指定处理人：张三」→ 跳过后续所有步骤。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u-zhang", "张三")]

        async def _go():
            ticket = _ticket(title="指定处理人：张三")
            return await flow.aassign(ticket, engs)

        result = asyncio.run(_go())
        assert result.engineer_id == "u-zhang"
        assert result.preferred_id == "u-zhang"
        assert result.matched_pref is True


class TestVagueSevereSkipsToStep7:
    """路径 B：dispatch_hint=severe → 跳过召回/精排/LLM，直接 Step7。"""

    def test_severe_dispatch_hint_goes_step7(self):
        """正常流程：severe 信号 → Step7 兜底（对接人优先于配置经理）。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲"), _eng("u2", "乙")]
        ticket = _ticket(dispatch_hint="severe")
        tighten_ret = _tighten_result(engs)

        async def _go():
            with patch.object(flow._tightener, "tighten", AsyncMock(return_value=tighten_ret)), \
                 patch.object(flow, "_resolve_contact_assignee", return_value="u1"), \
                 patch.object(flow, "_resolve_creator_id", return_value=None), \
                 patch.object(flow, "_resolve_prev_assignee_id", return_value=None), \
                 patch.object(flow, "_load_project_row", return_value=None), \
                 patch.object(flow, "_config_project_manager", return_value=("u-cfg", "配置经理")):
                return await flow.aassign(ticket, engs)

        result = asyncio.run(_go())
        # 对接人 u1 优先于配置经理
        assert result.engineer_id == "u1"
        assert result.decision_type == "fallback"


class TestStep7FallbackWhenLlmFails:
    """路径 C：LLM 决策失败 → Step7 兜底。"""

    def test_llm_none_falls_back_to_contact(self):
        """正常流程：Step6 LLM 返回 None → Step7 派对接人。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲"), _eng("u2", "乙")]
        ticket = _ticket()
        tighten_ret = _tighten_result(engs)

        async def _go():
            with patch.object(flow._tightener, "tighten", AsyncMock(return_value=tighten_ret)), \
                 patch.object(flow, "_resolve_contact_assignee", return_value="u1"), \
                 patch.object(flow, "_resolve_creator_id", return_value=None), \
                 patch.object(flow, "_resolve_prev_assignee_id", return_value=None), \
                 patch.object(flow._llm_recall, "arecall", AsyncMock(return_value=({}, {}))), \
                 patch.object(flow._history_recall, "arecall", AsyncMock(return_value={})), \
                 patch.object(flow._expertise_recall, "arecall", AsyncMock(return_value={})), \
                 patch.object(flow._llm_decision, "adecide", AsyncMock(return_value=None)), \
                 patch.object(flow, "_load_project_row", return_value=None), \
                 patch.object(flow, "_config_project_manager", return_value=None):
                return await flow.aassign(ticket, engs)

        result = asyncio.run(_go())
        assert result.engineer_id == "u1"
        assert result.decision_type == "fallback"


class TestLlmDecisionSuccess:
    """路径 D：Step6 LLM 决策成功 → 直接返回。"""

    def test_llm_decides_engineer(self):
        """正常流程：LLM 从候选中选人 → 返回 LLM 决策。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲"), _eng("u2", "乙")]
        ticket = _ticket()
        tighten_ret = _tighten_result(engs)

        llm_result = AssignmentResult(
            engineer_id="u2", engineer_name="乙",
            confidence_score=0.85, reasoning="乙负责路径规划",
            decision_type="auto",
        )

        async def _go():
            with patch.object(flow._tightener, "tighten", AsyncMock(return_value=tighten_ret)), \
                 patch.object(flow, "_resolve_contact_assignee", return_value=None), \
                 patch.object(flow, "_resolve_creator_id", return_value=None), \
                 patch.object(flow, "_resolve_prev_assignee_id", return_value=None), \
                 patch.object(flow._llm_recall, "arecall", AsyncMock(return_value=({"u1": 0.9}, {"u1": "熟悉路径规划"}))), \
                 patch.object(flow._history_recall, "arecall", AsyncMock(return_value={})), \
                 patch.object(flow._expertise_recall, "arecall", AsyncMock(return_value={})), \
                 patch.object(flow._llm_decision, "adecide", AsyncMock(return_value=llm_result)):
                return await flow.aassign(ticket, engs)

        result = asyncio.run(_go())
        assert result.engineer_id == "u2"
        assert result.decision_type == "auto"
        assert result.confidence_score == 0.85


class TestEmptyEngineersRaises:
    """异常流程：工程师列表为空 → ValueError。"""

    def test_empty_engineers_raises_value_error(self):
        flow = DispatchFlow()
        flow._config = _cfg()

        async def _go():
            return await flow.aassign(_ticket(), [])

        with pytest.raises(ValueError, match="工程师列表为空"):
            asyncio.run(_go())


class TestEmptyTitleAndDescriptionRaises:
    """异常流程：标题和描述都空 → ValueError。"""

    def test_empty_title_desc_raises(self):
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng()]

        async def _go():
            ticket = TicketContext(id="t", title="", problem_description="", status="new")
            return await flow.aassign(ticket, engs)

        with pytest.raises(ValueError, match="问题描述和标题均为空"):
            asyncio.run(_go())


class TestFinalizeAssignment:
    """_finalize_assignment：画像/候选快照/preferred 标记组装。"""

    def test_profile_attached_to_winner(self):
        """正常流程：最终结果挂载画像和候选快照。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲"), _eng("u2", "乙")]
        ticket = _ticket()
        result = AssignmentResult(
            engineer_id="u1", engineer_name="甲",
            confidence_score=0.9, reasoning="ok", decision_type="auto",
        )

        out = flow._finalize_assignment(
            ticket, result, engs, {}, "LLM决策", "[派单:t-e2e]",
            specified_unresolved=None, engineer_profiles=engs,
        )
        assert out.profile is not None
        assert out.profile["dept"] == "智能规划研究院"
        assert out.candidates is not None
        assert len(out.candidates) >= 2

    def test_preferred_matched_flag(self):
        """正常流程：preferred_assignee 命中 → matched_pref=True。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲")]
        ticket = _ticket(preferred_assignee="u1")
        result = AssignmentResult(
            engineer_id="u1", engineer_name="甲",
            confidence_score=0.9, reasoning="ok", decision_type="auto",
        )

        out = flow._finalize_assignment(
            ticket, result, engs, {}, "test", "[派单:t-e2e]",
            specified_unresolved=None, engineer_profiles=engs,
        )
        assert out.matched_pref is True

    def test_preferred_not_matched_flag(self):
        """正常流程：preferred_assignee 未命中 → matched_pref=False。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲")]
        ticket = _ticket(preferred_assignee="u-other")
        result = AssignmentResult(
            engineer_id="u1", engineer_name="甲",
            confidence_score=0.9, reasoning="ok", decision_type="auto",
        )

        out = flow._finalize_assignment(
            ticket, result, engs, {}, "test", "[派单:t-e2e]",
            specified_unresolved=None, engineer_profiles=engs,
        )
        assert out.matched_pref is False

    def test_no_preferred_assignee_no_flag(self):
        """正常流程：无 preferred_assignee → matched_pref 保持 None。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲")]
        ticket = _ticket()
        result = AssignmentResult(
            engineer_id="u1", engineer_name="甲",
            confidence_score=0.9, reasoning="ok", decision_type="auto",
        )

        out = flow._finalize_assignment(
            ticket, result, engs, {}, "test", "[派单:t-e2e]",
            specified_unresolved=None, engineer_profiles=engs,
        )
        assert out.matched_pref is None

    def test_specified_unresolved_in_profile(self):
        """正常流程：Step0 未命中的指定人名写入 profile.specified_name。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲")]
        ticket = _ticket()
        result = AssignmentResult(
            engineer_id="u1", engineer_name="甲",
            confidence_score=0.9, reasoning="ok", decision_type="fallback",
        )

        out = flow._finalize_assignment(
            ticket, result, engs, {}, "Step7兜底", "[派单:t-e2e]",
            specified_unresolved="王五", engineer_profiles=engs,
        )
        assert out.profile.get("specified_name") == "王五"

    def test_pref_incomplete_guard_in_profile(self):
        """正常流程：pref_incomplete_first_guard 标记写入 profile。"""
        flow = DispatchFlow()
        flow._config = _cfg()
        engs = [_eng("u1", "甲")]
        ticket = _ticket()
        result = AssignmentResult(
            engineer_id="u1", engineer_name="甲",
            confidence_score=0.9, reasoning="ok", decision_type="fallback",
        )

        out = flow._finalize_assignment(
            ticket, result, engs, {}, "Step7兜底", "[派单:t-e2e]",
            specified_unresolved=None, engineer_profiles=engs,
            pref_incomplete_first_guard=True,
        )
        assert out.profile.get("pref_incomplete_first_guard") is True
