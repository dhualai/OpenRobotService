"""Step1 部门主判 / 审查：工单字段同一套；部门画像只认库、yaml 不补漏。"""

from types import SimpleNamespace
from unittest.mock import patch

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
