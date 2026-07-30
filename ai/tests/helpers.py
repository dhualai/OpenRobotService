"""
共享测试工具 — 数据构造与流程模拟

从 test_can_submit.py 提取，供所有测试文件使用。
模拟函数复刻 pipeline 的核心决策链，用于快速单元测试。
"""

from ai.agents.AiDiagnosisPlatform.pipeline import (
    _can_submit, _check_required_fields, AgentState,
)


def make_state(phase="idle", problem_summary="", **kwargs):
    """快捷构造 AgentState"""
    defaults = dict(session_id="test-session", phase=phase, problem_summary=problem_summary)
    defaults.update(kwargs)
    return AgentState(**defaults)


def simulate_chat_flow(phase, problem_summary, user_query, llm_action,
                       llm_intent="troubleshoot", state_update=None,
                       llm_message="LLM原始回复"):
    """模拟 _agent_think 完整决策链（与真实代码顺序一致）

    返回 dict:
      - action: 最终动作 (submit/answer/ask)
      - message: 用户可见消息
      - phase: 变更后的 phase
      - submitted: 是否已提交
      - intercepted: 是否被闭环拦截
      - missing_fields: 必填字段缺失列表
    """
    state = make_state(phase, problem_summary)
    parsed = {
        "action": llm_action, "message": llm_message, "intent": llm_intent,
        "state_update": state_update or {}, "thinking": "",
    }

    # Step 1: _apply_state_update — LLM 提炼 problem_summary
    su = parsed["state_update"]
    if "problem_summary" in su:
        state.problem_summary = su["problem_summary"]

    # Step 2: _can_submit — 基于提炼后的 state
    can, reason = _can_submit(state)
    intercepted = False

    # Step 3: keyword + not can → intercept
    force_kw = ("转工单", "转单", "生成工单", "提交工单", "提单", "帮我转", "我要转")
    if any(kw in user_query for kw in force_kw) and not can:
        parsed["action"] = "answer"
        parsed["message"] = reason
        intercepted = True

    # Step 4: LLM submit + not can → intercept
    if parsed["action"] == "submit" and not can:
        parsed["action"] = "answer"
        parsed["message"] = reason
        intercepted = True

    # Step 5: _apply_action_phase
    if parsed["action"] == "submit":
        state.phase = "escalated"
    elif parsed["action"] == "answer":
        state.phase = "resolved"

    # Step 6: _force_submit_kw (only if can is True)
    if can and parsed["action"] != "submit" and any(kw in user_query for kw in force_kw):
        parsed["action"] = "submit"

    # Step 7: 模拟 _check_required_fields
    missing = []
    if parsed["action"] == "submit":
        if "collected_info" in (state_update or {}) and state_update["collected_info"].get("project"):
            pass
        else:
            missing.append("project")

    return {
        "action": parsed["action"],
        "message": parsed["message"],
        "phase": state.phase,
        "submitted": parsed["action"] == "submit" and not missing,
        "intercepted": intercepted,
        "missing_fields": missing if parsed["action"] == "submit" else [],
    }


def simulate_button_prepare(phase, problem_summary, collected_info=None):
    """模拟 prepare_ticket（按钮路径第一步）

    返回 dict:
      - blocked: 是否被闭环拦截
      - stage: "draft_ready" | "need_fields" | "blocked"
      - missing: 缺失字段列表
      - reason: 拦截原因
    """
    state = make_state(phase, problem_summary, collected_info=collected_info or {})
    can, reason = _can_submit(state)
    if not can:
        return {"blocked": True, "stage": "blocked", "missing": [], "reason": reason}

    ticket = {
        "project": state.collected_info.get("project", ""),
        "project_id": state.collected_info.get("project_id", ""),
    }
    check = _check_required_fields(ticket)
    return {
        "blocked": False,
        "stage": "draft_ready" if check["ok"] else "need_fields",
        "missing": check["missing"],
        "reason": check["prompt"],
    }


def simulate_button_confirm(phase, problem_summary, has_draft=True, overrides=None):
    """模拟 confirm_submit（按钮路径第二步）

    返回 dict:
      - ok: 是否提交成功
      - error: 错误原因
      - phase_after: 提交后的 phase
    """
    if not has_draft:
        return {"ok": False, "error": "没有待确认的工单草稿", "phase_after": phase}

    ticket = {"project": "", "project_id": ""}
    if overrides:
        ticket.update(overrides)
    check = _check_required_fields(ticket)
    if not check["ok"]:
        return {"ok": False, "error": check["prompt"], "missing": check["missing"],
                "phase_after": phase}

    return {"ok": True, "error": "", "phase_after": "resolved"}
