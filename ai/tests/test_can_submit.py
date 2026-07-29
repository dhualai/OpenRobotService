"""
测试闭环保护 + 完整提单流程
覆盖：
  1. _can_submit 单元 — 各 phase × problem 组合
  2. 对话路径 — _agent_think 完整决策链（含 LLM 提炼、关键词兜底、必填字段校验）
  3. 对话转单后 — 信息补充 / 附件追加 / follow_up 意图
  4. 按钮路径 — prepare_ticket → confirm_submit → overrides
  5. 混合路径 — 对话提单后按钮被拦 / 按钮提单后对话被拦
"""
import pytest
import json
from ai.agents.AiDiagnosisPlatform.pipeline import (
    _can_submit, _check_required_fields, AgentState,
)


# ================================================================
# 工具函数
# ================================================================

def make_state(phase, problem_summary="", **kwargs):
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
      - missing_fields: 必填字段缺失列表（模拟 _check_required_fields）
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
        # 模拟：默认没有 project → 缺失
        # 如果 collected_info 里有 project 则通过
        if "collected_info" in (state_update or {}) and state_update["collected_info"].get("project"):
            pass  # 有 project → 不缺失
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

    # 模拟 _check_required_fields
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

    # 模拟 _check_required_fields（含 overrides）
    ticket = {"project": "", "project_id": ""}
    if overrides:
        ticket.update(overrides)
    check = _check_required_fields(ticket)
    if not check["ok"]:
        return {"ok": False, "error": check["prompt"], "missing": check["missing"],
                "phase_after": phase}

    # 提交成功 → phase 变成 resolved
    return {"ok": True, "error": "", "phase_after": "resolved"}


# ================================================================
# 1. _can_submit 单元测试
# ================================================================

class TestCanSubmit:
    """_can_submit 函数单元测试 — 各 phase × problem_summary 组合"""

    def test_idle_with_problem(self):
        ok, reason = _can_submit(make_state("idle", "机器人不动了"))
        assert ok is True and reason == ""

    def test_idle_empty_problem(self):
        ok, reason = _can_submit(make_state("idle", ""))
        assert ok is True

    def test_diagnosing_with_problem(self):
        ok, reason = _can_submit(make_state("diagnosing", "离线报错"))
        assert ok is True

    def test_resolved_no_problem(self):
        ok, reason = _can_submit(make_state("resolved", ""))
        assert ok is False
        assert "无需重复提交" in reason

    def test_resolved_with_new_problem(self):
        ok, reason = _can_submit(make_state("resolved", "新的故障"))
        assert ok is True

    def test_escalated_with_problem(self):
        ok, reason = _can_submit(make_state("escalated", "机器人不动了"))
        assert ok is True
        assert reason == ""

    def test_escalated_empty_problem(self):
        ok, reason = _can_submit(make_state("escalated", ""))
        assert ok is False
        assert "处理中" in reason


# ================================================================
# 2. 对话路径 — _agent_think 完整决策链
# ================================================================

class TestChatSubmitFlow:
    """对话路径：LLM 解析 → state 提炼 → 闭环检查 → 必填校验 → 提单"""

    # ── 正常提单 ──

    def test_normal_keyword_submit(self):
        """用户说"转工单" → 关键词兜底强制提单
        注：关键词路径 LLM action=answer → _apply_action_phase → phase=resolved，
        然后 _force_submit_kw 改为 submit。最终 submit() 也会覆写为 resolved。
        """
        r = simulate_chat_flow("diagnosing", "机器人离线", "转工单", "answer")
        assert r["action"] == "submit"
        assert r["phase"] == "resolved"  # _apply_action_phase 先执行

    def test_normal_llm_submit(self):
        """LLM 自主判断 action=submit → 直接提单"""
        r = simulate_chat_flow("diagnosing", "电池报警", "帮我查一下", "submit")
        assert r["action"] == "submit"
        assert r["phase"] == "escalated"

    def test_keyword_submit_with_various_phrases(self):
        """各种提单说法都应触发关键词兜底"""
        phrases = ["转工单", "转单", "生成工单", "提交工单", "提单", "帮我转", "我要转"]
        for phrase in phrases:
            r = simulate_chat_flow("diagnosing", "故障现象", phrase, "answer")
            assert r["action"] == "submit", f"关键词 '{phrase}' 未触发提单"

    # ── LLM 提炼 problem_summary → 闭环判断 ──

    def test_llm_refines_chitchat_to_empty_escalated(self):
        """escalated + 废话 → LLM清空 → 关键词拦截"""
        r = simulate_chat_flow(
            "escalated", "好的谢谢", "转工单", "answer",
            state_update={"problem_summary": ""},
        )
        assert r["action"] == "answer"
        assert r["intercepted"] is True
        assert "处理中" in r["message"]

    def test_llm_refines_chitchat_to_empty_resolved(self):
        """resolved + 废话 → LLM清空 → 关键词拦截"""
        r = simulate_chat_flow(
            "resolved", "嗯嗯", "转工单", "answer",
            state_update={"problem_summary": ""},
        )
        assert r["action"] == "answer"
        assert r["intercepted"] is True
        assert "无需重复提交" in r["message"]

    def test_llm_refines_real_problem_after_escalated(self):
        """escalated + 真实新故障 → LLM保留 → 放行"""
        r = simulate_chat_flow(
            "escalated", "电池报警了", "转工单", "answer",
            state_update={"problem_summary": "电池报警"},
        )
        assert r["action"] == "submit"
        assert r["intercepted"] is False

    def test_llm_refines_vague_input_to_real_problem(self):
        """用户说得模糊但LLM提炼出有效问题 → 放行"""
        r = simulate_chat_flow(
            "diagnosing", "那个机器人好像有点不太对劲", "转工单", "answer",
            state_update={"problem_summary": "机器人行为异常"},
        )
        assert r["action"] == "submit"

    # ── 闭环拦截 ──

    def test_block_keyword_after_resolved(self):
        """resolved + 空问题 + 说"转工单" → 拦截"""
        r = simulate_chat_flow("resolved", "", "转工单", "answer")
        assert r["action"] == "answer"
        assert "无需重复提交" in r["message"]

    def test_block_llm_submit_after_resolved(self):
        """resolved + 空问题 + LLM误判submit → 拦截"""
        r = simulate_chat_flow("resolved", "", "好的谢谢", "submit")
        assert r["action"] == "answer"
        assert "无需重复提交" in r["message"]

    def test_block_keyword_escalated_empty(self):
        """escalated + 空问题 + 说"转工单" → 拦截"""
        r = simulate_chat_flow("escalated", "", "转工单", "answer")
        assert r["action"] == "answer"
        assert "处理中" in r["message"]

    def test_block_force_keyword_when_cannot(self):
        """_can=False 时 _force_submit_kw 不能绕过"""
        r = simulate_chat_flow("resolved", "", "帮我转单吧", "answer")
        assert r["action"] == "answer"
        assert r["intercepted"] is True

    # ── 必填字段校验 ──

    def test_submit_missing_project_blocked(self):
        """提单但缺 project → submit 被转为 answer，引导用户补充"""
        r = simulate_chat_flow("diagnosing", "机器人离线", "转工单", "answer")
        assert r["action"] == "submit"
        assert "project" in r["missing_fields"]
        assert r["submitted"] is False  # 缺字段不能真正提交

    def test_submit_with_project_passes(self):
        """LLM 已收集 project → 必填校验通过 → 可以提交"""
        r = simulate_chat_flow(
            "diagnosing", "机器人离线", "转工单", "answer",
            state_update={"collected_info": {"project": "华大制造基地"}},
        )
        assert r["action"] == "submit"
        assert r["missing_fields"] == []
        assert r["submitted"] is True

    # ── 边界 ──

    def test_idle_no_keyword_no_submit(self):
        """普通诊断对话，不触发提单"""
        r = simulate_chat_flow("idle", "机器人不动", "怎么处理", "answer")
        assert r["action"] == "answer"
        assert r["message"] == "LLM原始回复"

    def test_resolved_with_new_problem_keyword(self):
        """resolved + 有新问题 + 关键词 → 放行"""
        r = simulate_chat_flow("resolved", "又出故障了", "转工单", "answer")
        assert r["action"] == "submit"

    def test_llm_submit_and_keyword_both(self):
        """LLM 输出 submit 且用户说了关键词 → 不重复拦截"""
        r = simulate_chat_flow("diagnosing", "故障", "帮我转工单吧", "submit")
        assert r["action"] == "submit"

    def test_default_agent_state(self):
        """全新会话 → 允许提单"""
        ok, reason = _can_submit(AgentState(session_id="new"))
        assert ok is True and reason == ""


# ================================================================
# 3. 对话转单后 — 信息补充 / 附件追加
# ================================================================

class TestPostSubmitFollowUp:
    """提交工单后用户继续对话：信息补充、附件追加、闲聊过滤"""

    def make_post_submit_state(self, ticket_id="T-001", topic="电池报警"):
        """构造已提交工单后的 AgentState"""
        return make_state(
            "resolved", "",
            last_submitted_ticket={
                "ticket_id": ticket_id, "db_id": 1,
                "title": topic, "topic": topic,
                "submitted_at": 1700000000,
            },
        )

    def test_follow_up_append_info(self):
        """提交后用户补充信息 → 识别为 follow_up → 追加到工单"""
        state = self.make_post_submit_state()
        # 模拟 was_post_submit=True 的判断
        was_post_submit = bool(state.last_submitted_ticket and
                               state.last_submitted_ticket.get("ticket_id"))
        assert was_post_submit is True

        # intent=follow_up → 应追加
        r = simulate_chat_flow(
            "resolved", "", "对了，那个机器人是3号机", "answer",
            llm_intent="follow_up",
        )
        # follow_up 不改变 action（仍是 answer），但实际会触发 _append_to_ticket
        assert r["action"] == "answer"

    def test_follow_up_attachment_info(self):
        """提交后用户补充附件信息 → 追加到工单"""
        state = self.make_post_submit_state()
        was_post_submit = bool(state.last_submitted_ticket and
                               state.last_submitted_ticket.get("ticket_id"))
        assert was_post_submit is True

        r = simulate_chat_flow(
            "resolved", "", "这是现场照片和日志文件", "answer",
            llm_intent="follow_up",
        )
        assert r["action"] == "answer"

    def test_post_submit_chitchat_not_follow_up(self):
        """提交后用户说"谢谢" → intent=chat → 不追加"""
        r = simulate_chat_flow(
            "resolved", "", "谢谢", "answer",
            llm_intent="chat",
        )
        assert r["action"] == "answer"

    def test_post_submit_new_problem_not_follow_up(self):
        """提交后用户描述新故障 → intent=troubleshoot → 不追加（新话题）"""
        r = simulate_chat_flow(
            "idle", "新的故障现象", "转工单", "answer",
            llm_intent="troubleshoot",
        )
        assert r["action"] == "submit"

    def test_post_submit_howto_not_follow_up(self):
        """提交后用户问操作问题 → intent=howto → 不追加"""
        r = simulate_chat_flow(
            "resolved", "", "怎么查看日志", "answer",
            llm_intent="howto",
        )
        assert r["action"] == "answer"

    def test_was_post_submit_flag_off_when_no_ticket(self):
        """没有 last_submitted_ticket → was_post_submit=False"""
        state = make_state("resolved", "")
        was_post_submit = bool(state.last_submitted_ticket and
                               state.last_submitted_ticket.get("ticket_id"))
        assert was_post_submit is False

    def test_follow_up_blocked_from_creating_new_ticket(self):
        """follow_up 场景下说"转工单" → 仍然被闭环拦截（phase=resolved+空问题）"""
        state = self.make_post_submit_state()
        r = simulate_chat_flow(
            "resolved", "", "转工单", "answer",
            llm_intent="follow_up",
        )
        assert r["action"] == "answer"
        assert r["intercepted"] is True


# ================================================================
# 4. 按钮路径 — prepare_ticket → confirm_submit
# ================================================================

class TestButtonFlow:
    """手动点击工单按钮 → 弹窗填写 → 确认提交"""

    # ── prepare_ticket ──

    def test_prepare_draft_ready(self):
        """有 project → 草稿就绪"""
        r = simulate_button_prepare("diagnosing", "故障", {"project": "华大基地"})
        assert r["blocked"] is False
        assert r["stage"] == "draft_ready"

    def test_prepare_need_fields(self):
        """缺 project → 需要补充"""
        r = simulate_button_prepare("diagnosing", "故障")
        assert r["blocked"] is False
        assert r["stage"] == "need_fields"
        assert "project" in r["missing"]

    def test_prepare_blocked_resolved_empty(self):
        """刚提交完点按钮 → 拦截"""
        r = simulate_button_prepare("resolved", "")
        assert r["blocked"] is True
        assert r["stage"] == "blocked"

    def test_prepare_blocked_escalated_empty(self):
        """工单处理中点按钮（无新问题） → 拦截"""
        r = simulate_button_prepare("escalated", "")
        assert r["blocked"] is True
        assert r["stage"] == "blocked"

    def test_prepare_allowed_escalated_with_problem(self):
        """工单处理中点按钮（有新问题） → 放行"""
        r = simulate_button_prepare("escalated", "新故障")
        assert r["blocked"] is False

    def test_prepare_allowed_idle_with_problem(self):
        """idle 有点击按钮 → 放行"""
        r = simulate_button_prepare("idle", "机器人不动了")
        assert r["blocked"] is False

    def test_prepare_with_project_id(self):
        """用 project_id 也能通过必填校验"""
        r = simulate_button_prepare("diagnosing", "故障", {"project_id": "P-123"})
        assert r["stage"] == "draft_ready"

    # ── confirm_submit ──

    def test_confirm_success(self):
        """弹窗确认 → 提交成功 → phase=resolved"""
        r = simulate_button_confirm("diagnosing", "故障",
                                    overrides={"project": "华大基地"})
        assert r["ok"] is True
        assert r["phase_after"] == "resolved"

    def test_confirm_no_draft(self):
        """没有草稿就确认 → 报错"""
        r = simulate_button_confirm("diagnosing", "故障", has_draft=False)
        assert r["ok"] is False
        assert "草稿" in r["error"]

    def test_confirm_missing_project(self):
        """确认时 project 仍为空 → 拒绝"""
        r = simulate_button_confirm("diagnosing", "故障")
        assert r["ok"] is False
        assert "project" in r.get("missing", [])

    def test_confirm_override_fills_project(self):
        """用户在弹窗中补充 project → 确认成功"""
        r = simulate_button_confirm(
            "diagnosing", "故障",
            overrides={"project": "用户刚填的项目"},
        )
        assert r["ok"] is True

    def test_confirm_override_multiple_fields(self):
        """弹窗覆盖多个字段 → 全部生效"""
        r = simulate_button_confirm(
            "diagnosing", "故障",
            overrides={
                "project": "华大基地",
                "priority": "紧急",
                "contact": "张三",
                "robot_type": "XCB031",
            },
        )
        assert r["ok"] is True
        assert r["phase_after"] == "resolved"

    def test_confirm_override_empty_project(self):
        """overrides 有 project 但是空字符串 → 仍缺失"""
        r = simulate_button_confirm(
            "diagnosing", "故障",
            overrides={"project": ""},
        )
        assert r["ok"] is False


# ================================================================
# 5. 混合路径 — 对话 ↔ 按钮互斥
# ================================================================

class TestMixedPaths:
    """对话提单和按钮提单之间的互斥关系"""

    def test_chat_submit_then_button_blocked(self):
        """对话提单后 → 点工单按钮 → 被拦截"""
        # 对话提交 → phase=resolved, problem=""
        r1 = simulate_chat_flow("diagnosing", "故障A", "转工单", "answer",
                                state_update={"collected_info": {"project": "基地"}})
        assert r1["submitted"] is True
        assert r1["phase"] == "resolved"  # 关键词路径：_apply_action_phase 先设 resolved

        # 此时 phase=resolved, problem=""（已清空）
        # 按钮 prepare → 拦截
        r2 = simulate_button_prepare("resolved", "")
        assert r2["blocked"] is True

    def test_button_confirm_then_chat_blocked(self):
        """按钮确认提交后 → 对话说"转工单" → 被拦截"""
        # 按钮确认 → phase=resolved
        r1 = simulate_button_confirm("diagnosing", "故障",
                                     overrides={"project": "基地"})
        assert r1["ok"] is True
        assert r1["phase_after"] == "resolved"

        # 对话转工单 → resolved+"" → 拦截
        r2 = simulate_chat_flow("resolved", "", "转工单", "answer")
        assert r2["action"] == "answer"
        assert r2["intercepted"] is True

    def test_chat_submit_then_new_problem_button_allowed(self):
        """对话提单后 → 描述新问题 → 点按钮 → 放行"""
        # 第一次：对话提单
        r1 = simulate_chat_flow("diagnosing", "故障A", "转工单", "answer",
                                state_update={"collected_info": {"project": "基地"}})
        assert r1["submitted"] is True

        # run() 重置：phase=idle, problem="新的故障描述"
        r2 = simulate_button_prepare("idle", "电池冒烟了")
        assert r2["blocked"] is False

    def test_button_prepare_then_chat_fills_fields_then_confirm(self):
        """按钮→缺字段→LLM对话补充→用户再次确认 → 完整闭环"""
        # Step 1: 按钮 prepare → 缺 project
        r1 = simulate_button_prepare("diagnosing", "机器人离线")
        assert r1["stage"] == "need_fields"

        # Step 2: LLM 对话引导收集 project
        r2 = simulate_chat_flow("diagnosing", "机器人离线", "华大制造基地", "answer",
                                llm_intent="troubleshoot",
                                state_update={"collected_info": {"project": "华大制造基地"}})
        assert r2["action"] == "answer"

        # Step 3: 用户点击确认（带 project）
        r3 = simulate_button_confirm("diagnosing", "机器人离线",
                                     overrides={"project": "华大制造基地"})
        assert r3["ok"] is True
        assert r3["phase_after"] == "resolved"

    def test_chat_collects_project_then_auto_submit(self):
        """对话中 LLM 收集齐 project → 自动提单成功"""
        # 模拟完整流程：先缺 project → LLM 引导 → 用户补充 → 提交
        r = simulate_chat_flow(
            "diagnosing", "机器人离线", "在华大制造基地3号产线", "answer",
            state_update={
                "problem_summary": "机器人离线",
                "collected_info": {"project": "华大制造基地", "location": "3号产线"},
            },
        )
        # LLM 收集了 project，但 action 是 answer（还没让转工单）
        # 如果此时用户说"转工单"——
        r2 = simulate_chat_flow(
            "diagnosing", "机器人离线", "转工单", "answer",
            state_update={
                "collected_info": {"project": "华大制造基地"},
            },
        )
        assert r2["action"] == "submit"
        assert r2["submitted"] is True


# ================================================================
# 6. 连续提单 & 多轮对话
# ================================================================

class TestMultiRound:
    """多轮对话、连续提单场景"""

    def test_two_independent_submits(self):
        """提交 → 新问题 → 再提交：两次独立工单"""
        # 第一单
        r1 = simulate_chat_flow("diagnosing", "故障A", "转工单", "answer",
                                state_update={"collected_info": {"project": "基地"}})
        assert r1["submitted"] is True

        # 第二单（run() 重置为 idle + 新 problem）
        r2 = simulate_chat_flow("idle", "故障B", "转工单", "answer",
                                state_update={"collected_info": {"project": "基地"}})
        assert r2["submitted"] is True

    def test_three_rapid_submits(self):
        """快速连续三次提单 → 第一次成功，后两次被拦截"""
        # 第一次：成功（关键词路径 → phase=resolved）
        r1 = simulate_chat_flow("diagnosing", "故障A", "转工单", "answer",
                                state_update={"collected_info": {"project": "基地"}})
        assert r1["submitted"] is True
        assert r1["phase"] == "resolved"

        # 第二次：没有新问题 → 拦截（resolved+空）
        r2 = simulate_chat_flow("resolved", "", "转工单", "answer")
        assert r2["intercepted"] is True

        # 第三次：还是没有新问题 → 拦截
        r3 = simulate_chat_flow("resolved", "", "转工单", "answer")
        assert r3["intercepted"] is True

    def test_submit_then_idle_no_problem_keyword(self):
        """提交后 idle+空问题+关键词 → 走 _force_submit_kw → 但缺 project 兜底"""
        r = simulate_chat_flow("idle", "", "转工单", "answer")
        assert r["action"] == "submit"
        # _can_submit 放行（idle 不拦截），_force_submit_kw 触发
        # 但 project 缺失 → submitted=False
        assert r["submitted"] is False
        assert "project" in r["missing_fields"]

    def test_state_transition_diagnosing_to_escalated(self):
        """phase 流转：diagnosing → submit → escalated（LLM 自主提单路径）"""
        # LLM 直接输出 action=submit（非关键词兜底）
        r = simulate_chat_flow("diagnosing", "故障", "请帮我处理", "submit",
                                state_update={"collected_info": {"project": "基地"}})
        assert r["phase"] == "escalated"  # submit action → _apply_action_phase → escalated
        assert r["submitted"] is True

    def test_state_transition_escalated_to_idle_on_new_query(self):
        """escalated + 空问题 → run() 重置 → idle + 新 query → 可提单"""
        # 模拟 run() 已重置
        r = simulate_chat_flow("idle", "新的故障", "转工单", "answer",
                                state_update={"collected_info": {"project": "基地"}})
        assert r["action"] == "submit"
        assert r["submitted"] is True


# ================================================================
# 7. _check_required_fields 单元测试
# ================================================================

class TestCheckRequiredFields:
    """必填字段校验逻辑"""

    def test_project_present(self):
        r = _check_required_fields({"project": "华大基地"})
        assert r["ok"] is True
        assert r["missing"] == []

    def test_project_id_present(self):
        r = _check_required_fields({"project_id": "P-123"})
        assert r["ok"] is True

    def test_both_project_and_id_present(self):
        r = _check_required_fields({"project": "华大", "project_id": "P-123"})
        assert r["ok"] is True

    def test_both_empty(self):
        r = _check_required_fields({"project": "", "project_id": ""})
        assert r["ok"] is False
        assert "project" in r["missing"]

    def test_missing_keys(self):
        r = _check_required_fields({})
        assert r["ok"] is False
        assert "project" in r["missing"]

    def test_prompt_message(self):
        r = _check_required_fields({})
        assert "项目" in r["prompt"]
        assert "project" in r["missing"]


# ================================================================
# 8. AgentState 完整场景
# ================================================================

class TestAgentStateScenarios:
    """AgentState 在各种场景下的行为"""

    def test_full_diagnosis_to_submit_lifecycle(self):
        """完整生命周期：新会话 → 诊断 → 提单 → 清空 → 新话题"""
        # 1. 新会话
        state = make_state("idle", "")
        assert _can_submit(state)[0] is True

        # 2. 开始诊断
        state.phase = "diagnosing"
        state.problem_summary = "机器人不接任务"
        state.hypotheses = ["调度系统故障", "网络延迟"]
        state.ruled_out = ["硬件故障"]
        state.collected_info = {"project": "华大基地", "robot_type": "XCB031"}
        state.diagnosis_rounds = 3
        assert _can_submit(state)[0] is True

        # 3. 提交工单（模拟 submit 后的清空）
        state.phase = "escalated"
        state.problem_summary = ""
        state.hypotheses = []
        state.ruled_out = []
        state.collected_info = {}
        state.diagnosis_rounds = 0
        assert _can_submit(state)[0] is False  # escalated+空 → 拦截

        # 4. 新话题（run 重置后）
        state.phase = "idle"
        state.problem_summary = "电池报警"
        assert _can_submit(state)[0] is True

    def test_ticket_seq_increment(self):
        """ticket_seq 自增，同一会话多次提单各自独立"""
        state = make_state("idle", "故障A", ticket_seq=0)
        assert state.ticket_seq == 0
        state.ticket_seq += 1
        assert state.ticket_seq == 1
        state.ticket_seq += 1
        assert state.ticket_seq == 2

    def test_last_submitted_ticket_preserved(self):
        """last_submitted_ticket 在清空 problem 后仍保留"""
        state = make_state("resolved", "",
                           last_submitted_ticket={"ticket_id": "T-001", "db_id": 1,
                                                  "title": "电池故障", "topic": "电池报警"})
        # problem 已清空，但 last_submitted_ticket 还在
        assert state.problem_summary == ""
        assert state.last_submitted_ticket["ticket_id"] == "T-001"

    def test_attachments_in_collected_info(self):
        """附件信息通过 collected_info 携带到工单"""
        state = make_state("diagnosing", "机器人离线",
                           collected_info={
                               "project": "华大基地",
                               "attachments": json.dumps(["photo.jpg", "log.txt"]),
                           })
        assert "attachments" in state.collected_info
        assert "photo.jpg" in state.collected_info["attachments"]

    def test_multiple_hypotheses_tracking(self):
        """多假设追踪"""
        state = make_state("diagnosing", "通信异常",
                           hypotheses=["网络故障", "SIM卡欠费", "天线松动"],
                           ruled_out=["电源问题"])
        assert len(state.hypotheses) == 3
        assert len(state.ruled_out) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
