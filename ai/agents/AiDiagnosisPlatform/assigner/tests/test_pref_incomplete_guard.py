"""倾向人画像不完整：首次护栏 tip + 再次确认直派。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import (
    DeptRoutingResult,
    ProductRoutingResult,
    TightenResult,
)
from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult,
    EngineerProfile,
    TicketContext,
)
from app.services.redispatch_tip_service import build_redispatch_tip


def _ticket(**kw) -> TicketContext:
    base = dict(
        id="805",
        title="车辆故障",
        problem_description="界面无报错",
        status="new",
        preferred_assignee="u-liuyi",
    )
    base.update(kw)
    return TicketContext(**base)


def _pool_eng() -> EngineerProfile:
    return EngineerProfile(
        id="u-other",
        name="马晓庆",
        department="智能移动研究院",
        job_level=1,
        responsibility_modules={"车端软件": {"控制": ["车端控制"]}},
    )


def test_incomplete_preferred_first_sets_guard_flag():
    """正常流程：首次倾向人不在准入池 → 继续派单并打 pref_incomplete_first_guard。"""
    flow = DispatchFlow()
    pool = [_pool_eng()]
    ticket = _ticket()

    async def _go():
        with patch.object(DispatchFlow, "_detect_preferred_assignee", return_value=(None, None)), \
             patch.object(DispatchFlow, "_prev_dispatch_preferred_id", return_value=None), \
             patch.object(flow, "_resolve_contact_assignee", return_value=None), \
             patch.object(flow._tightener, "tighten") as tighten, \
             patch(
                 "ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow.match_vague_strong_signal",
                 return_value=True,
             ), \
             patch.object(flow, "_run_step7") as step7:
            tighten.return_value = TightenResult(
                candidates=pool, before_count=1, after_count=1,
                dept=DeptRoutingResult(mode="no_filter"),
                product=ProductRoutingResult(mode="no_filter"),
            )
            step7.return_value = AssignmentResult(
                engineer_id="u-other",
                engineer_name="马晓庆",
                confidence_score=0.0,
                reasoning="兜底",
                decision_type="fallback",
            )
            return await flow.aassign(ticket, pool)

    result = asyncio.run(_go())
    assert result.preferred_id == "u-liuyi"
    assert result.matched_pref is False
    assert (result.profile or {}).get("pref_incomplete_first_guard") is True


def test_incomplete_preferred_second_direct_assign():
    """正常流程：同一倾向人再次选择（画像不全）→ 无条件直派。"""
    flow = DispatchFlow()
    pool = [_pool_eng()]
    ticket = _ticket()
    everyone = [{
        "id": "u-liuyi",
        "name": "刘义",
        "department": None,
        "job_level": 0,
        "responsibility_modules": {},
    }]

    async def _go():
        with patch.object(DispatchFlow, "_detect_preferred_assignee", return_value=(None, None)), \
             patch.object(DispatchFlow, "_prev_dispatch_preferred_id", return_value="u-liuyi"), \
             patch(
                 "ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync._fetch_from_users_table",
                 return_value=everyone,
             ):
            return await flow.aassign(ticket, pool)

    result = asyncio.run(_go())
    assert result.engineer_id == "u-liuyi"
    assert result.engineer_name == "刘义"
    assert result.matched_pref is True
    assert result.decision_type == "auto"
    assert "连续两次" in result.reasoning


def test_complete_preferred_second_also_direct_assign():
    """正常流程：同一倾向人再次选择（画像完整）→ 同样无条件直派，不走智能派单。"""
    flow = DispatchFlow()
    complete = EngineerProfile(
        id="u-liuyi",
        name="刘义",
        department="智能移动研究院",
        job_level=1,
        responsibility_modules={"车端软件": {"界面": ["异常提示"]}},
    )
    pool = [_pool_eng(), complete]
    ticket = _ticket()
    everyone = [{
        "id": "u-liuyi",
        "name": "刘义",
        "department": "智能移动研究院",
        "job_level": 1,
        "responsibility_modules": {"车端软件": {"界面": ["异常提示"]}},
    }]

    async def _go():
        with patch.object(DispatchFlow, "_detect_preferred_assignee", return_value=(None, None)), \
             patch.object(DispatchFlow, "_prev_dispatch_preferred_id", return_value="u-liuyi"), \
             patch(
                 "ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync._fetch_from_users_table",
                 return_value=everyone,
             ):
            return await flow.aassign(ticket, pool)

    result = asyncio.run(_go())
    assert result.engineer_id == "u-liuyi"
    assert result.matched_pref is True
    assert "连续两次" in result.reasoning
    assert "画像不完整" not in result.reasoning


def test_tip_pref_incomplete_first_guard():
    """正常流程：首次护栏 tip 文案。"""
    log = SimpleNamespace(
        assigned_id="u-other",
        preferred_id="u-liuyi",
        matched_pref=False,
        pinyin_match=False,
        name_collision=False,
        reasoning="当前画像下难以判定",
        profile={"pref_incomplete_first_guard": True},
        candidates=[],
    )
    tip = build_redispatch_tip(log, {"u-other": "齐子谦", "u-liuyi": "刘义"})
    assert tip is not None
    assert "画像不完整" in tip
    assert "首次将不纳入智能派单" in tip
    assert "再次发起重新派单" in tip
    assert "齐子谦" in tip
    assert "刘义" in tip
