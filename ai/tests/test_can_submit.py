"""
测试闭环保护 + 完整提单流程（LLM 驱动版）

信任模型：其余字段是否齐全交给 LLM 的 ticket_ready，服务端只守 project 铁律 +
闭环保护（last_submitted_ticket + 新 problem）。已无关键词 force-submit。

覆盖：
  1. _can_submit 单元 — last_submitted_ticket × problem 组合
  2. 对话路径 — _agent_think 决策链（LLM action=submit / 闭环 / project 铁律）
  3. 按钮路径 — prepare_ticket → confirm_submit
  4. 混合路径 — 对话/按钮互斥（共享 last_submitted_ticket）
"""
import pytest
import json
from ai.agents.AiDiagnosisPlatform.pipeline import (
    _can_submit, _check_required_fields, _assess_ticket_readiness, AgentState,
)


# ================================================================
# 工具函数
# ================================================================

def make_state(phase="idle", problem_summary="", **kwargs):
    """快捷构造 AgentState"""
    defaults = dict(session_id="test-session", phase=phase, problem_summary=problem_summary)
    defaults.update(kwargs)
    return AgentState(**defaults)


def simulate_chat_flow(phase, problem_summary, user_query, llm_action,
                       llm_intent="troubleshoot", state_update=None,
                       llm_message="LLM原始回复", last_submitted_ticket=None,
                       collected_info=None, ticket_collecting=None):
    """模拟 _agent_think 决策链（与真实代码顺序一致）。

    新逻辑要点：
      - 无关键词 force-submit；submit 只来自 LLM action=submit（或 ticket_collecting 收齐）
      - 闭环：last_submitted_ticket 非空 + 无新 problem → 拦截
      - project 铁律：submit 时 collected_info 缺 project → 转 ask
    """
    state = make_state(
        phase, problem_summary,
        last_submitted_ticket=last_submitted_ticket or {},
        collected_info=dict(collected_info or {}),
        ticket_collecting=list(ticket_collecting or []),
    )
    parsed = {
        "action": llm_action, "message": llm_message, "intent": llm_intent,
        "state_update": state_update or {}, "thinking": "",
    }

    # Step 1: 应用 state_update
    su = parsed["state_update"]
    if "problem_summary" in su:
        state.problem_summary = su["problem_summary"]
    if "collected_info" in su:
        for k, v in su["collected_info"].items():
            if v:
                state.collected_info[k] = v

    # Step 2: 闭环保护（last_submitted_ticket + 新 problem）
    can, reason = _can_submit(state)
    intercepted = False
    if parsed["action"] == "submit" and not can:
        parsed["action"] = "answer"
        parsed["message"] = reason
        intercepted = True

    # Step 3: project 铁律（submit 时 collected_info 必须有 project）
    missing = []
    if parsed["action"] == "submit":
        ready, miss = _assess_ticket_readiness(state)
        if not ready:
            missing = miss
            parsed["action"] = "ask"

    # Step 4: phase 转换
    if parsed["action"] == "submit":
        state.phase = "escalated"
    elif parsed["action"] == "answer":
        state.phase = "resolved"

    return {
        "action": parsed["action"],
        "message": parsed["message"],
        "phase": state.phase,
        "submitted": parsed["action"] == "submit",
        "intercepted": intercepted,
        "missing_fields": missing if parsed["action"] != "submit" else [],
    }


def simulate_button_prepare(phase, problem_summary, collected_info=None,
                            last_submitted_ticket=None):
    """模拟 prepare_ticket（按钮路径第一步）。"""
    state = make_state(phase, problem_summary,
                       collected_info=collected_info or {},
                       last_submitted_ticket=last_submitted_ticket or {})
    can, reason = _can_submit(state)
    if not can:
        return {"blocked": True, "stage": "blocked", "missing": [], "reason": reason}
    ready, miss = _assess_ticket_readiness(state)
    return {
        "blocked": False,
        "stage": "draft_ready" if ready else "need_fields",
        "missing": miss,
        "reason": "请给出工单关联的项目名称。" if not ready else "",
    }


def simulate_button_confirm(phase, problem_summary, has_draft=True, overrides=None,
                            last_submitted_ticket=None):
    """模拟 confirm_submit（按钮路径第二步）。"""
    if not has_draft:
        return {"ok": False, "error": "没有待确认的工单草稿", "phase_after": phase}
    state = make_state(phase, problem_summary,
                       last_submitted_ticket=last_submitted_ticket or {})
    can, reason = _can_submit(state)
    if not can:
        return {"ok": False, "error": reason, "phase_after": phase}
    ticket = {"project": "", "project_id": ""}
    if overrides:
        ticket.update(overrides)
    check = _check_required_fields(ticket)
    if not check["ok"]:
        return {"ok": False, "error": check["prompt"], "missing": check["missing"],
                "phase_after": phase}
    return {"ok": True, "error": "", "phase_after": "resolved"}


# ================================================================
# 1. _can_submit 单元测试（基于 last_submitted_ticket + 新 problem）
# ================================================================

class TestCanSubmit:
    """_can_submit：刚提完单（last_submitted_ticket）+ 无新问题 → 拦截"""

    def test_no_prior_ticket_allows(self):
        """从没提过单 → 一律允许（不管 phase/problem）"""
        assert _can_submit(make_state("idle", ""))[0] is True
        assert _can_submit(make_state("resolved", ""))[0] is True
        assert _can_submit(make_state("diagnosing", "故障"))[0] is True

    def test_just_submitted_no_new_problem_blocks(self):
        """刚提完单 + 无新 problem → 拦截"""
        st = make_state("resolved", "", last_submitted_ticket={"ticket_id": "T-1"})
        ok, reason = _can_submit(st)
        assert ok is False
        assert "新问题" in reason or "新现象" in reason

    def test_just_submitted_with_new_problem_allows(self):
        """刚提完单 + 描述了新 problem → 放行（重新开始提单）"""
        st = make_state("resolved", "另一台车报错",
                        last_submitted_ticket={"ticket_id": "T-1"})
        assert _can_submit(st)[0] is True

    def test_new_session_allows(self):
        """全新会话 → 允许"""
        assert _can_submit(AgentState(session_id="new"))[0] is True


# ================================================================
# 2. 对话路径 — _agent_think 决策链
# ================================================================

class TestChatSubmitFlow:
    """对话路径：LLM action=submit → 闭环 → project 铁律 → 提单"""

    def test_llm_submit_with_project(self):
        """LLM action=submit + 有 project → 提单成功"""
        r = simulate_chat_flow("diagnosing", "机器人离线", "帮我提单", "submit",
                               collected_info={"project": "华大基地"})
        assert r["action"] == "submit"
        assert r["submitted"] is True

    def test_llm_submit_missing_project_blocked(self):
        """LLM action=submit 但缺 project → 转 ask，引导补项目"""
        r = simulate_chat_flow("diagnosing", "机器人离线", "帮我提单", "submit")
        assert r["action"] == "ask"
        assert "项目名称" in r["missing_fields"]

    def test_llm_submit_after_just_submitted_intercepted(self):
        """刚提完单（last_submitted_ticket）+ LLM 又 submit + 无新问题 → 闭环拦截"""
        r = simulate_chat_flow("resolved", "", "再转一个", "submit",
                               last_submitted_ticket={"ticket_id": "T-1"})
        assert r["action"] == "answer"
        assert r["intercepted"] is True

    def test_llm_submit_new_problem_after_submit_allowed(self):
        """刚提完单 + 描述新问题 + submit → 放行"""
        r = simulate_chat_flow("resolved", "另一台车报错", "转工单", "submit",
                               last_submitted_ticket={"ticket_id": "T-1"},
                               collected_info={"project": "华大基地"})
        assert r["action"] == "submit"

    def test_llm_answer_stays_answer(self):
        """LLM 输出 answer（不提单）→ 保持 answer，不因关键词强制提单"""
        r = simulate_chat_flow("diagnosing", "故障", "转工单", "answer")
        assert r["action"] == "answer"
        assert r["submitted"] is False

    def test_normal_query_no_submit(self):
        """普通诊断对话 → answer，不提单"""
        r = simulate_chat_flow("idle", "机器人不动", "怎么处理", "answer")
        assert r["action"] == "answer"


# ================================================================
# 3. 按钮路径 — prepare_ticket → confirm_submit
# ================================================================

class TestButtonFlow:
    """按钮路径：闭环 + project 铁律"""

    def test_prepare_draft_ready(self):
        """有 project → 草稿就绪"""
        r = simulate_button_prepare("diagnosing", "故障", {"project": "华大基地"})
        assert r["blocked"] is False
        assert r["stage"] == "draft_ready"

    def test_prepare_need_fields(self):
        """缺 project → 需要补充"""
        r = simulate_button_prepare("diagnosing", "故障")
        assert r["stage"] == "need_fields"
        assert "项目名称" in r["missing"]

    def test_prepare_blocked_after_submit(self):
        """刚提完单（last_submitted_ticket）+ 无新问题 → 拦截"""
        r = simulate_button_prepare("resolved", "",
                                    last_submitted_ticket={"ticket_id": "T-1"})
        assert r["blocked"] is True

    def test_prepare_allowed_with_new_problem(self):
        """刚提完单 + 有新问题 → 放行"""
        r = simulate_button_prepare("resolved", "新故障",
                                    last_submitted_ticket={"ticket_id": "T-1"},
                                    collected_info={"project": "基地"})
        assert r["blocked"] is False

    def test_prepare_with_project_id(self):
        """用 project_id 也能通过"""
        r = simulate_button_prepare("diagnosing", "故障", {"project_id": "P-123"})
        assert r["stage"] == "draft_ready"

    def test_confirm_success(self):
        """弹窗确认（带 project）→ 成功"""
        r = simulate_button_confirm("diagnosing", "故障",
                                    overrides={"project": "华大基地"})
        assert r["ok"] is True
        assert r["phase_after"] == "resolved"

    def test_confirm_no_draft(self):
        r = simulate_button_confirm("diagnosing", "故障", has_draft=False)
        assert r["ok"] is False
        assert "草稿" in r["error"]

    def test_confirm_missing_project(self):
        r = simulate_button_confirm("diagnosing", "故障")
        assert r["ok"] is False
        assert "project" in r["missing"]


# ================================================================
# 4. 混合路径 — 对话 ↔ 按钮互斥（共享 last_submitted_ticket）
# ================================================================

class TestMixedPaths:
    """对话提单与按钮提单共享 last_submitted_ticket，互斥一致"""

    def test_chat_submit_then_button_blocked(self):
        """对话提单后（last_submitted_ticket 已设）→ 点按钮 → 拦截"""
        r1 = simulate_chat_flow("diagnosing", "故障A", "转工单", "submit",
                                collected_info={"project": "基地"})
        assert r1["submitted"] is True
        # 模拟 submit 后：last_submitted_ticket 已设，problem 清空
        r2 = simulate_button_prepare("resolved", "",
                                     last_submitted_ticket={"ticket_id": "T-1"})
        assert r2["blocked"] is True

    def test_button_confirm_then_chat_blocked(self):
        """按钮确认提单后 → 对话再提单（无新问题）→ 拦截"""
        r2 = simulate_chat_flow("resolved", "", "转工单", "submit",
                                last_submitted_ticket={"ticket_id": "T-1"})
        assert r2["action"] == "answer"
        assert r2["intercepted"] is True

    def test_chat_submit_then_new_problem_allowed(self):
        """对话提单后 → 描述新问题 → 放行（重新开始提单）"""
        r = simulate_chat_flow("resolved", "电池冒烟了", "转工单", "submit",
                               last_submitted_ticket={"ticket_id": "T-1"},
                               collected_info={"project": "基地"})
        assert r["action"] == "submit"

    def test_dual_path_consistency(self):
        """同一 last_submitted_ticket 状态下，对话与按钮拦截结果一致"""
        lst = {"ticket_id": "T-1"}
        chat = simulate_chat_flow("resolved", "", "转工单", "submit",
                                  last_submitted_ticket=lst)
        button = simulate_button_prepare("resolved", "", last_submitted_ticket=lst)
        assert chat["intercepted"] is True
        assert button["blocked"] is True


# ================================================================
# 5. _check_required_fields 单元测试
# ================================================================

class TestCheckRequiredFields:
    """project 铁律校验"""

    def test_project_present(self):
        assert _check_required_fields({"project": "华大基地"})["ok"] is True

    def test_project_id_present(self):
        assert _check_required_fields({"project_id": "P-123"})["ok"] is True

    def test_both_empty(self):
        r = _check_required_fields({"project": "", "project_id": ""})
        assert r["ok"] is False
        assert "project" in r["missing"]

    def test_missing_keys(self):
        assert _check_required_fields({})["ok"] is False

    def test_prompt_message(self):
        assert "项目" in _check_required_fields({})["prompt"]


# ================================================================
# 6. AgentState 场景
# ================================================================

class TestAgentStateScenarios:

    def test_submit_lifecycle(self):
        """新会话 → 诊断 → 提单（设 last_submitted_ticket）→ 新问题放行"""
        # 新会话
        assert _can_submit(make_state("idle", ""))[0] is True
        # 诊断中
        assert _can_submit(make_state("diagnosing", "故障",
                                      collected_info={"project": "基地"}))[0] is True
        # 提单后：last_submitted_ticket 设 + problem 清空 → 拦截
        assert _can_submit(make_state("resolved", "",
                                      last_submitted_ticket={"ticket_id": "T-1"}))[0] is False
        # 新问题 → 放行
        assert _can_submit(make_state("resolved", "新故障",
                                      last_submitted_ticket={"ticket_id": "T-1"}))[0] is True

    def test_ticket_seq_increment(self):
        state = make_state("idle", "故障A", ticket_seq=0)
        state.ticket_seq += 1
        assert state.ticket_seq == 1

    def test_last_submitted_ticket_preserved(self):
        state = make_state("resolved", "",
                           last_submitted_ticket={"ticket_id": "T-001"})
        assert state.last_submitted_ticket["ticket_id"] == "T-001"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
