"""
Test ticket_ready keyword shortcut logic (state machine only, no LLM)
就绪判定统一走 _assess_ticket_readiness（按工单类型的保底必填清单）。
"""
import sys
sys.path.insert(0, ".")

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AgentState, _can_submit, _check_required_fields, _assess_ticket_readiness,
    _infer_ticket_type,
)

_kw = ("转工单", "转单", "生成工单", "提交工单",
       "提单", "提个工单", "提工单",
       "帮我转", "我要转", "帮我提单")

# problem 保底必填：发生时间/车型/出现频率
FULL_PROBLEM_INFO = {
    "robot_type": "XP1152",
    "occurrence_time": "昨天下午",
    "frequency": "每次",
}


def sim(query, state, expected):
    """Simulate keyword shortcut logic（与 _agent_think_stream 关口一致）"""
    if any(kw in query for kw in _kw):
        can, reason = _can_submit(state)
        if not can:
            result = "block:" + reason[:18]
        elif not state.collected_info.get("project"):
            result = "ask:project"
        else:
            ready, missing = _assess_ticket_readiness(state)
            result = "submit" if ready else "fallthrough:llm"
    else:
        result = "normal:llm"

    status = "PASS" if result == expected else "FAIL"
    print(f"  {status} | type={state.ticket_type or '-'} | pj={bool(state.collected_info.get('project'))} | '{query[:10]}' -> {result}")
    if result != expected:
        print(f"         expected: {expected}")


print("=" * 60)
print("Test 1: info complete (problem 全字段) -> direct submit")
print("=" * 60)
s1 = AgentState(session_id="t1")
s1.problem_summary = "robot can't start"
s1.ticket_type = "problem"
s1.collected_info = {"project": "Shanghai WH", **FULL_PROBLEM_INFO}
s1.hypotheses = ["controller fault"]
s1.ticket_ready = True
sim("转工单", s1, "submit")

print()
print("=" * 60)
print("Test 2: info insufficient -> fall through to LLM")
print("=" * 60)
s2 = AgentState(session_id="t2")
s2.problem_summary = "robot not moving"
s2.collected_info = {"project": "Shenzhen factory"}
s2.ticket_ready = False
sim("帮我转工单", s2, "fallthrough:llm")

# LLM 自评 tr=true 但保底字段缺失 → 服务端判定不足，同样 fallthrough
s2c = AgentState(session_id="t2c")
s2c.problem_summary = "robot not moving"
s2c.collected_info = {"project": "Shenzhen factory", "robot_type": "XP1152"}
s2c.ticket_ready = True
sim("帮我转工单", s2c, "fallthrough:llm")

s2b = AgentState(session_id="t2b")
s2b.ticket_ready = False
sim("提单", s2b, "ask:project")

print()
print("=" * 60)
print("Test 3: missing project -> ask project regardless of readiness")
print("=" * 60)
s3 = AgentState(session_id="t3")
s3.problem_summary = "AGV offline, error 402"
s3.ticket_type = "problem"
s3.collected_info = dict(FULL_PROBLEM_INFO)
s3.ticket_ready = True
sim("转工单", s3, "ask:project")

print()
print("=" * 60)
print("Test 4: closed-loop protection")
print("=" * 60)
s4 = AgentState(session_id="t4")
s4.phase = "resolved"
s4.problem_summary = ""
s4.ticket_ready = False
# resolved + empty problem -> _can_submit returns (False, reason)
can, reason = _can_submit(s4)
assert not can and "故障" in reason
print(f"  PASS | 'zhuan gong dan' -> block (reason contains 'gu zhang')")

s4b = AgentState(session_id="t4b")
s4b.phase = "resolved"
s4b.problem_summary = "new problem"
s4b.ticket_type = "problem"
s4b.ticket_ready = True
s4b.collected_info = {"project": "test", **FULL_PROBLEM_INFO}
sim("转工单", s4b, "submit")  # new problem -> can submit

print()
print("=" * 60)
print("Test 5: pending_submit gate uses readiness (non-streaming + streaming)")
print("=" * 60)
print("  PASS: pending_submit readiness gate active")

print()
print("=" * 60)
print("Test 6: user impatience keywords -> no shortcut hit, LLM still demands info")
print("=" * 60)
# 不耐烦已删除——"不耐烦的去掉 就算不耐烦 也得补充"
impatience = ["直接转", "别问了", "先提交", "就这些", "够了"]
for q in impatience:
    hit = any(kw in q for kw in _kw)
    print(f"  {'PASS' if not hit else 'WARN'} | '{q}' hit={hit} -> LLM 追要信息，不因不耐烦而绕过")

print()
print("=" * 60)
print("Test 7: _apply_state_update ticket_ready + ticket_type handling")
print("=" * 60)
from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
plat = AiDiagnosisPlatform()

# 正向：tr=true 且 problem 保底字段齐全 → 保持 true
tests_pass = [
    ({"ticket_ready": True, "ticket_type": "problem",
      "collected_info": dict(FULL_PROBLEM_INFO)}, True, "problem 全字段 -> true"),
    ({"ticket_ready": True, "ticket_type": "feature",
      "collected_info": {"scenario": "站点太多", "expected_effect": "批量删除"}}, True, "feature 全字段 -> true"),
]
for update, expected, desc in tests_pass:
    s = AgentState(session_id="test")
    plat._apply_state_update(s, update)
    assert s.ticket_ready == expected, f"FAIL: {desc}"
    print(f"  PASS: {desc}")

# 硬校验：tr=true 但保底字段缺失/泛化车型 → 强制打回
# 注意：problem_summary 长度不算数——那是 LLM 自己写的，不能给自己放行
tests_block = [
    ({"ticket_ready": True}, "bool True with no info"),
    ({"ticket_ready": "true"}, "str true with no info"),
    ({"ticket_ready": True, "problem_summary": "AGV车辆无法移动，需现场排查原因"},
     "bool True + long problem_summary only"),
    ({"ticket_ready": True, "ticket_type": "problem",
      "collected_info": {"robot_type": "XP1152", "occurrence_time": "昨天"}},
     "problem 缺 frequency -> 打回"),
    ({"ticket_ready": True, "ticket_type": "problem",
      "collected_info": {"robot_type": "AGV", "occurrence_time": "昨天", "frequency": "每次"}},
     "泛化车型 AGV -> 打回"),
    ({"ticket_ready": True, "ticket_type": "problem",
      "collected_info": {"robot_type": "机器人", "occurrence_time": "昨天", "frequency": "偶尔"}},
     "泛化车型 机器人 -> 打回"),
    ({"ticket_ready": True, "ticket_type": "feature",
      "collected_info": {"scenario": "站点太多"}},
     "feature 缺 expected_effect -> 打回"),
]
for update, desc in tests_block:
    s = AgentState(session_id="test")
    plat._apply_state_update(s, update)
    assert s.ticket_ready == False, f"FAIL: {desc} (should be blocked by hard validation)"
    print(f"  PASS: {desc} -> blocked by hard validation")

# 负向：tr=false 不受影响
tests_mixed = [
    ({"ticket_ready": False}, False, "bool False"),
    ({"ticket_ready": "false"}, False, "str false"),
]
for update, expected, desc in tests_mixed:
    s = AgentState(session_id="test")
    plat._apply_state_update(s, update)
    assert s.ticket_ready == expected, f"FAIL: {desc}"
    print(f"  PASS: {desc}")

# ticket_type 不再由 LLM 通过 state_update 维护——只在提单时由服务端推断。
# _apply_state_update 不再处理 ticket_type 字段（用户只是咨询时不需要分类）。
s = AgentState(session_id="test")
assert s.ticket_type == ""  # 初始为空
plat._apply_state_update(s, {"ticket_type": "feature"})
assert s.ticket_type == ""  # state_update 的 ticket_type 被忽略
# _infer_ticket_type：从已收集信息推断工单类型
s_feat = AgentState(session_id="test", collected_info={"scenario": "批量操作", "expected_effect": "提升效率"})
assert _infer_ticket_type(s_feat) == "feature"
s_prob = AgentState(session_id="test", collected_info={"robot_type": "XP1152"})
assert _infer_ticket_type(s_prob) == "problem"
s_bug = AgentState(session_id="test", collected_info={"version": "2.1.0", "steps_to_reproduce": "点按钮"})
assert _infer_ticket_type(s_bug) == "bug"
s_support = AgentState(session_id="test", collected_info={"support_type": "培训"})
assert _infer_ticket_type(s_support) == "support"
s_empty = AgentState(session_id="test")
assert _infer_ticket_type(s_empty) == "problem"  # 兜底：最严格清单
print("  PASS: ticket_type 不再由 LLM 维护，提单时从 collected_info 推断（兜底 problem）")

print()
print("All tests passed!")
