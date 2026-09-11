"""Step1 部门主判 / 审查：工单字段同一套；部门画像只认库、yaml 不补漏。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.dept_audit_signal import DeptAuditSignal
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.dept_ticket_prompt import ticket_fields_block
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.llm_dept_signal import LlmDeptSignal
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig


def _ticket() -> TicketContext:
    return TicketContext(
        id="t-step1",
        title="后台加导出按钮",
        problem_description="希望每周能导出日报",
        status="new",
        ticket_type="feature",
        scenario="运营每周导出",
        expected_effect="一键导出 excel",
        diagnosis_hypotheses=["缺导出入口", "权限未开放"],
        project_name="摇人吧服务号",
        fault_code="",
        robot_type="",
    )


def test_r2_and_audit_share_ticket_fields():
    """正常流程：审查与 R2 看同一段【工单】字段（含类型、场景、Agent假设）。"""
    ticket = _ticket()
    block = ticket_fields_block(ticket)
    r2 = LlmDeptSignal()._build_prompt(ticket)
    audit = DeptAuditSignal()._build_prompt(ticket, "智能规划研究院")
    assert "工单类型：feature" in block
    assert "需求场景：运营每周导出" in block
    assert "Agent假设：缺导出入口；权限未开放" in block
    assert "故障码" not in block
    assert "车型" not in block
    assert block in r2
    assert block in audit
    assert "报障(problem)、缺陷(bug)：看【故障现象】" in r2
    assert "需求(feature)、咨询(support)：看【工单涉及的产品/项目】" in r2
    assert "本单是需求(feature)" in r2
    assert "以正文为准重选尺子" in r2
    assert "Agent假设仅供参考" in r2
    assert "原样复制" in r2
    assert "1) 定尺子" in r2
    assert "0.80~1.0：明确负责（只有这一档才会触发部门硬收紧）" in r2
    assert "【产品归属】" in r2
    assert "车端硬件" in r2 and "机器人事业部" in r2
    assert "车端软件" in r2 and "智能移动研究院" in r2
    assert "调度USP" in r2 and "智能规划研究院" in r2
    assert "【产品归属】" in audit
    assert "放在一起对照" in audit
    assert "不要先单独认层再判部门" in audit
    assert "报障(problem)、缺陷(bug)：看【故障现象】" in audit
    assert "本单是需求(feature)" in audit
    assert "Agent假设仅供参考" in audit
    assert "原样复制" in audit


def test_ticket_fields_use_diagnosis_collected():
    """正常流程：诊断摘要和 collected_info 进工单段；已有栏去重，指名不进。"""
    ticket = TicketContext(
        id="t-diag",
        title="车子停了",
        problem_description="在货架前不动",
        status="new",
        ticket_type="problem",
        robot_type="",
        fault_code="",
        diagnosis_problem_summary="车子在货架前停住起不来",
        diagnosis_collected_info={
            "robot_type": "S20",
            "fault_code": "E1001",
            "occurrence_time": "昨天傍晚",
            "frequency": "每天两三次",
            "requested_assignee": "张三",
            "project": "某某调度现场",
            "special_notes": "无",
        },
        diagnosis_hypotheses=["电机过热"],
        project_name="现场A",
    )
    block = ticket_fields_block(ticket)
    assert "诊断摘要：车子在货架前停住起不来" in block
    assert "车型：S20" in block
    assert "故障码：E1001" in block
    assert "故障时间：昨天傍晚" in block
    assert "出现频率：每天两三次" in block
    assert "张三" not in block
    assert "某某调度现场" not in block
    assert "特殊说明" not in block
    assert "Agent假设：电机过热" in block


def test_ticket_fields_skip_duplicate_summary():
    """正常流程：摘要与标题相同则不重复带。"""
    ticket = _ticket()
    ticket.diagnosis_problem_summary = ticket.title
    block = ticket_fields_block(ticket)
    assert "诊断摘要" not in block


def test_departments_empty_db_no_yaml_fallback():
    """异常流程：库里没有部门画像 → 不用 yaml 补，标记 missing。"""
    with patch.object(AssignerConfig, "_load_departments_from_db", return_value=[]):
        cfg = AssignerConfig()
    assert cfg.departments == []
    assert cfg.dept_profiles_missing is True


def test_departments_db_only_not_merged_with_yaml():
    """正常流程：库里有画像只认库，不把 yaml 里同名/其它部门补进来。"""
    only = [{"name": "仅库里有的部", "profile_text": "负责测试", "examples": []}]
    with patch.object(AssignerConfig, "_load_departments_from_db", return_value=only):
        cfg = AssignerConfig()
    names = {d.get("name") for d in cfg.departments}
    assert names == {"仅库里有的部"}
    assert "机器人事业部" not in names
    assert cfg.dept_profiles_missing is False


def test_hard_when_primary_score_ge_080_ignores_margin():
    """正常流程：主部门分 ≥ 0.80 即 hard；0.70～0.80 需 margin > 0.20。"""
    from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter

    router = DeptRouter(config=SimpleNamespace(
        departments=[{"name": "智能规划研究院"}],
        departments_without_profile=[],
        dept_profiles_missing=False,
        dept_audit_enabled=False,
        department_routing={
            "thresholds": {
                "hard_filter_score": 0.80,
                "hard_mid_score": 0.70,
                "hard_filter_margin": 0.20,
                "soft_prior_score": 0.55,
            },
            "fusion": {},
        },
    ))
    assert router._decide_mode("智能规划研究院", 0.85, 0.05) == "hard_filter"
    assert router._decide_mode("智能规划研究院", 0.80, 0.0) == "hard_filter"
    assert router._decide_mode("智能规划研究院", 0.75, 0.21) == "hard_filter"
    assert router._decide_mode("智能规划研究院", 0.70, 0.25) == "hard_filter"
    assert router._decide_mode("智能规划研究院", 0.75, 0.20) == "soft_prior"  # 须 >0.20
    assert router._decide_mode("智能规划研究院", 0.69, 0.50) == "soft_prior"
    assert router._decide_mode("智能规划研究院", 0.79, 0.10) == "soft_prior"


def test_audit_redo_same_dept_respects_thresholds():
    """异常流程：审查打回后仍是原部门，低置信不能无条件 hard_filter。"""
    import asyncio
    from unittest.mock import AsyncMock

    from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter
    from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.dept_audit_signal import (
        DeptAuditResult,
    )
    from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile

    cfg = SimpleNamespace(
        departments=[{"name": "智能规划研究院"}],
        departments_without_profile=[],
        dept_profiles_missing=False,
        dept_audit_enabled=True,
        department_routing={
            "thresholds": {
                "hard_filter_score": 0.80,
                "soft_prior_score": 0.55,
            },
            "fusion": {"history_bonus": 0.05, "history_confirm_threshold": 0.5},
            "audit": {"min_confidence": 0.7},
        },
    )
    router = DeptRouter(config=cfg)
    router._llm.classify = AsyncMock(return_value={"智能规划研究院": 0.62})
    router._history.aggregate = AsyncMock(return_value={})
    router._audit.audit = AsyncMock(return_value=DeptAuditResult(
        ok=False, correct_dept="", confidence=0.4, reason="不太确定",
    ))
    engs = [EngineerProfile(id="u-a", name="甲", department="智能规划研究院")]
    _cands, result = asyncio.run(router.route(_ticket(), engs))
    assert result.mode == "soft_prior"
    assert result.signals.get("audit_redone") is True


def test_audit_failed_hard_degrades_to_soft():
    """异常流程：审查失败与打回异常一致 — hard → soft_prior。"""
    import asyncio
    from unittest.mock import AsyncMock

    from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter
    from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.dept_audit_signal import (
        DeptAuditResult,
    )
    from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile

    cfg = SimpleNamespace(
        departments=[{"name": "智能规划研究院"}],
        departments_without_profile=[],
        dept_profiles_missing=False,
        dept_audit_enabled=True,
        department_routing={
            "thresholds": {
                "hard_filter_score": 0.80,
                "hard_mid_score": 0.70,
                "hard_filter_margin": 0.20,
                "soft_prior_score": 0.55,
            },
            "fusion": {"history_bonus": 0.05, "history_confirm_threshold": 0.5},
            "audit": {"min_confidence": 0.7},
        },
    )
    router = DeptRouter(config=cfg)
    router._llm.classify = AsyncMock(return_value={"智能规划研究院": 0.90})
    router._history.aggregate = AsyncMock(return_value={})
    router._audit.audit = AsyncMock(return_value=DeptAuditResult(
        audit_failed=True, reason="审查LLM调用失败",
    ))
    engs = [
        EngineerProfile(id="u-a", name="甲", department="智能规划研究院"),
        EngineerProfile(id="u-b", name="乙", department="智能移动研究院"),
    ]
    cands, result = asyncio.run(router.route(_ticket(), engs))
    assert result.mode == "soft_prior"
    assert result.primary_dept == "智能规划研究院"
    assert {e.id for e in cands} == {"u-a", "u-b"}


def test_audit_correct_requires_070():
    """正常流程：纠正 conf≥0.7 才强制 hard；0.65 不够则打回重判。"""
    import asyncio
    from unittest.mock import AsyncMock

    from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter
    from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.dept_audit_signal import (
        DeptAuditResult,
    )
    from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile

    cfg = SimpleNamespace(
        departments=[{"name": "智能规划研究院"}, {"name": "机器人事业部"}],
        departments_without_profile=[],
        dept_profiles_missing=False,
        dept_audit_enabled=True,
        department_routing={
            "thresholds": {
                "hard_filter_score": 0.80,
                "hard_mid_score": 0.70,
                "hard_filter_margin": 0.20,
                "soft_prior_score": 0.55,
            },
            "fusion": {},
            "audit": {"min_confidence": 0.7},
        },
    )
    router = DeptRouter(config=cfg)
    router._llm.classify = AsyncMock(side_effect=[
        {"智能规划研究院": 0.90},
        {"机器人事业部": 0.88},
    ])
    router._history.aggregate = AsyncMock(return_value={})
    router._audit.audit = AsyncMock(return_value=DeptAuditResult(
        ok=False, correct_dept="机器人事业部", confidence=0.65, reason="更像硬件",
    ))
    engs = [
        EngineerProfile(id="u-a", name="甲", department="智能规划研究院"),
        EngineerProfile(id="u-h", name="硬", department="机器人事业部"),
    ]
    _cands, result = asyncio.run(router.route(_ticket(), engs))
    assert result.signals.get("audit_corrected") is not True
    assert result.signals.get("audit_redone") is True
    assert result.primary_dept == "机器人事业部"
    assert result.mode == "hard_filter"


def test_reload_config_clears_history_sync():
    """正常流程：热更新同时清 history_sync 与人员画像缓存。"""
    from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow

    flow = DispatchFlow.__new__(DispatchFlow)
    flow._config = MagicMock()
    with patch.object(flow._config, "reload"), patch(
        "ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow.invalidate_expertise_cache"
    ), patch(
        "ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync.invalidate_cache"
    ) as hs, patch(
        "ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync.invalidate_cache"
    ) as pers:
        flow.reload_config()
    hs.assert_called_once()
    pers.assert_called_once()


def test_no_dept_profile_tip():
    """正常流程：没有部门画像 → tip 提醒去后台补。"""
    from app.services.redispatch_tip_service import build_redispatch_tip

    log = SimpleNamespace(
        assigned_id="u-1",
        preferred_id=None,
        pinyin_match=False,
        name_collision=False,
        profile={"no_dept_profile": True},
    )
    assert build_redispatch_tip(log, {"u-1": "张三"}) == "没有部门画像，请到后台补充部门职责"
