"""Step7 兜底：对接人 → 本单项目经理 → 配置项目经理；都不在则未指派 + tip。

不调 LLM。查项目 / 用户表用 mock。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_step7.py -v
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow
from ai.agents.AiDiagnosisPlatform.assigner.ranking.fallback_decision import (
    FallbackDecision,
    REASON_STEP6,
    REASON_VAGUE,
)
from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import match_vague_strong_signal
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _eng(eid: str, name: str) -> EngineerProfile:
    return EngineerProfile(
        id=eid,
        name=name,
        department="智能规划研究院",
        job_level=1,
        responsibility_modules={"摇人吧服务号": {"前端": ["页面"]}},
        duty_text="负责前端",
    )


def _ticket(**kwargs) -> TicketContext:
    data = {
        "id": "t-step7",
        "title": "现场报障",
        "problem_description": "车子停了",
        "status": "new",
        "project_name": "测试项目",
    }
    data.update(kwargs)
    return TicketContext(**data)


class TestFallbackOrder:
    """只认对接人 / 项目经理 / 配置经理，不看精排。"""

    def test_contact_wins(self):
        """正常流程：有对接人 → 派对接人，理由原样带上。"""
        out = FallbackDecision().decide(
            contact_id="u-c", contact_name="对接",
            project_pm_id="u-pm", project_pm_name="经理",
            config_pm_id="u-cfg", config_pm_name="配置",
            reason=REASON_STEP6,
        )
        assert out is not None
        assert out.engineer_id == "u-c"
        assert out.engineer_name == "对接"
        assert out.reasoning == REASON_STEP6
        assert out.decision_type == "fallback"
        assert out.confidence_score == 0.0

    def test_project_pm_if_no_contact(self):
        """正常流程：无对接人 → 派本单项目经理。"""
        out = FallbackDecision().decide(
            project_pm_id="u-pm", project_pm_name="经理",
            config_pm_id="u-cfg", config_pm_name="配置",
            reason=REASON_VAGUE,
        )
        assert out.engineer_id == "u-pm"
        assert out.reasoning == REASON_VAGUE

    def test_config_pm_last(self):
        """正常流程：项目字段也空 → 用配置项目经理。"""
        out = FallbackDecision().decide(
            config_pm_id="u-cfg", config_pm_name="配置",
            reason=REASON_STEP6,
        )
        assert out.engineer_id == "u-cfg"

    def test_all_empty_is_none(self):
        """异常流程：三者都空 → None，不编精排 #1。"""
        assert FallbackDecision().decide(reason=REASON_STEP6) is None


class TestVagueSkipFlag:
    """只认 dispatch_hint=severe 才跳过 3–6。"""

    def test_description_no_skip(self):
        """正常流程：描述含旧标记也不截断。"""
        cfg = SimpleNamespace(vague_strong_signals={"enabled": True})
        assert match_vague_strong_signal(
            _ticket(problem_description="车子停了 [问题描述不完整]"), cfg,
        ) is False

    def test_dispatch_hint_severe_would_skip(self):
        """正常流程：诊断 dispatch_hint=severe → 主流程将跳过 Step3–6。"""
        cfg = SimpleNamespace(vague_strong_signals={"enabled": True})
        assert match_vague_strong_signal(_ticket(dispatch_hint="severe"), cfg) is True
        assert match_vague_strong_signal(_ticket(dispatch_hint="lacking"), cfg) is False


class TestRunStep7:
    """DispatchFlow._run_step7 接线：有人就派，没人就未指派 + tip。"""

    def test_uses_project_pm_not_ranking(self):
        """正常流程：无对接人时派项目表上的经理，不看精排。"""
        flow = DispatchFlow()
        row = SimpleNamespace(project_manager_id="u-pm", project_manager="现场经理")
        with patch.object(DispatchFlow, "_load_project_row", return_value=row), \
             patch.object(DispatchFlow, "_config_project_manager", return_value=("u-cfg", "配置")):
            out = flow._run_step7(
                _ticket(), None, None, [_eng("u-other", "路人")], REASON_STEP6, "[派单:t-step7]",
            )
        assert out.engineer_id == "u-pm"
        assert out.engineer_name == "现场经理"
        assert "精排" not in (out.reasoning or "")

    def test_fallback_person_need_not_be_in_pool(self):
        """正常流程：兜底人可以不在智能派单准入池（画像不必完整）。"""
        flow = DispatchFlow()
        row = SimpleNamespace(project_manager_id="u-pm", project_manager="现场经理")
        with patch.object(DispatchFlow, "_load_project_row", return_value=row), \
             patch.object(DispatchFlow, "_config_project_manager", return_value=None):
            out = flow._run_step7(
                _ticket(), "u-out", "编外对接", [_eng("u-other", "路人")],
                REASON_STEP6, "[派单:t-step7]",
            )
        assert out.engineer_id == "u-out"
        assert out.engineer_name == "编外对接"

    def test_empty_returns_unassignable(self):
        """异常流程：对接人、项目经理、配置都空 → 未指派结果，写 tip 标记，不抛错。"""
        flow = DispatchFlow()
        with patch.object(DispatchFlow, "_load_project_row", return_value=None), \
             patch.object(DispatchFlow, "_config_project_manager", return_value=None):
            out = flow._run_step7(
                _ticket(), None, None, [_eng("u-a", "甲")], REASON_STEP6, "[派单:t-step7]",
            )
        assert out.engineer_id == ""
        assert out.decision_type == "fallback"
        assert (out.profile or {}).get("unassignable") is True
        assert "对接人" in (out.reasoning or "")


class TestUnassignableTip:
    """GET 出口：失败也要给提单人 tip。"""

    def test_unassignable_tip(self):
        """异常流程：profile.unassignable → 失败说明，不编接单人。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="",
            preferred_id=None,
            pinyin_match=False,
            name_collision=False,
            profile={"unassignable": True},
        )
        tip = build_redispatch_tip(log, {})
        assert tip is not None
        assert "暂时无法派单" in tip
        assert "对接人" in tip

    def test_unassignable_beats_specified_name(self):
        """异常流程：无人可派优先于「没找到指定人」。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="",
            preferred_id=None,
            pinyin_match=False,
            name_collision=False,
            profile={"unassignable": True, "specified_name": "赵不存在"},
        )
        tip = build_redispatch_tip(log, {})
        assert "暂时无法派单" in tip
        assert "已按智能派单处理" not in tip


class TestDiagnosisFromMeta:
    """诊断落库是 metadata_info.diagnosis 嵌套对象。"""

    def test_reads_nested_diagnosis_not_flat_keys(self):
        """正常流程：从 diagnosis.hypotheses 取值，不读顶层 diagnosis_hypotheses。"""
        from ai.agents.AiDiagnosisPlatform.assigner.pipeline.worker import (
            _diagnosis_from_meta,
        )
        meta = {
            "diagnosis_hypotheses": ["错误平铺，不该读"],
            "diagnosis": {
                "problem_summary": "车子停了",
                "hypotheses": ["电机过热", "调度锁死"],
                "ruled_out": ["电池没电"],
                "collected_info": {"robot_type": "S20"},
                "rounds": 3,
            },
        }
        out = _diagnosis_from_meta(meta)
        assert out["diagnosis_hypotheses"] == ["电机过热", "调度锁死"]
        assert out["diagnosis_ruled_out"] == ["电池没电"]
        assert out["diagnosis_collected_info"] == {"robot_type": "S20"}
        assert out["diagnosis_problem_summary"] == "车子停了"
        assert out["diagnosis_rounds"] == 3

    def test_empty_diagnosis(self):
        """异常流程：没有 diagnosis → 全空。"""
        from ai.agents.AiDiagnosisPlatform.assigner.pipeline.worker import (
            _diagnosis_from_meta,
        )
        assert _diagnosis_from_meta({})["diagnosis_hypotheses"] is None
        assert _diagnosis_from_meta(None)["diagnosis_rounds"] is None
