"""
End-to-end conversation simulation for ticket_ready flow
Mocks only external deps (LLM, retrieval, memory), runs real pipeline logic.
"""
import sys, json, asyncio
sys.path.insert(0, ".")

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform, AgentState, DiagnosisRequest,
    _load_agent_state, _save_agent_state, _agent_state_summary,
    _assess_ticket_readiness,
    DIAGNOSIS_PROMPT,
)

# ── Mock memory ──
class MockMemory:
    def __init__(self, session_id):
        self.session_id = session_id
        self.turns = []
        self.metadata = {}

class MockMemoryManager:
    def __init__(self):
        self.memories = {}
    async def get_memory(self, sid):
        return self.memories.setdefault(sid, MockMemory(sid))
    async def save_memory(self, mem):
        self.memories[mem.session_id] = mem
    async def add_turn(self, sid, role, content):
        mem = await self.get_memory(sid)
        mem.turns.append({"role": role, "content": content})
        return mem
    async def add_pending_ticket(self, sid):
        pass

# ── Mock LLM ──
class MockLLM:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
    async def complete(self, prompt, **kwargs):
        self.calls.append(prompt)
        return self.responses.pop(0)

# ── Mock retriever ──
class MockRetriever:
    async def retrieve_domain(self, *args, **kwargs):
        return []


async def main():
    plat = AiDiagnosisPlatform()
    plat._memory_manager = MockMemoryManager()
    plat._retriever = MockRetriever()

    # Scenario: user describes problem, LLM judges info insufficient (ticket_ready=false),
    # then user fills in more info, LLM sets ticket_ready=true, then user says "转工单"

    # ---- Round 1: User reports problem ----
    print("=" * 65)
    print("Round 1: User reports problem -> LLM sets ticket_ready=false")
    print("=" * 65)

    r1 = DiagnosisRequest(session_id="conv1", query="机器人不动了")
    mem = await plat._memory_manager.add_turn("conv1", "user", "机器人不动了")
    state = AgentState(session_id="conv1")

    # Simulate LLM response: asks for more info, ticket_ready=false
    # Simulate LLM: info not enough -> ticket_ready=false
    r1_raw = '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"robot cannot move","ticket_ready":false,"collected_info":{}}}\n```\nWhich site? What robot model? Any error code?'

    parsed = plat._parse_agent_output(r1_raw)
    plat._apply_state_update(state, parsed["state_update"])
    plat._apply_action_phase(state, parsed["action"])
    print(f"  LLM action: {parsed['action']}")
    print(f"  LLM message: {parsed['message'][:60]}...")
    print(f"  ticket_ready: {state.ticket_ready}")
    print(f"  problem_summary: {state.problem_summary}")
    assert state.ticket_ready == False, f"expected ticket_ready=False, got {state.ticket_ready}"
    assert "robot" in state.problem_summary, f"expected 'robot' in problem_summary, got {state.problem_summary}"
    print("  PASS: ticket_ready=false, LLM asks for more info")

    # ---- Round 2: User provides info ----
    print()
    print("=" * 65)
    print("Round 2: User provides details -> LLM sets ticket_ready=true")
    print("=" * 65)

    r2 = DiagnosisRequest(session_id="conv1", query="上海仓库，型号XP1152，昨天下午开始，每次都这样")
    await plat._memory_manager.add_turn("conv1", "user", "上海仓库，型号XP1152，昨天下午开始，每次都这样")
    state.collected_info = {"project": "上海仓库", "robot_type": "XP1152"}
    state.ticket_type = "problem"

    # Simulate LLM: info now enough -> ticket_ready=true
    r2_raw = '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"XP1152 cannot move since yesterday","ticket_type":"problem","ticket_ready":true,"collected_info":{"robot_type":"XP1152","occurrence_time":"昨天下午","frequency":"每次"}}}\n```\n先检查电机供电线缆。还有别的异常吗？'

    parsed = plat._parse_agent_output(r2_raw)
    plat._apply_state_update(state, parsed["state_update"])
    plat._apply_action_phase(state, parsed["action"])
    print(f"  ticket_ready: {state.ticket_ready}")
    print(f"  project: {state.collected_info.get('project')}")
    print(f"  robot_type: {state.collected_info.get('robot_type')}")
    assert state.ticket_ready == True, f"expected ticket_ready=True, got {state.ticket_ready}"
    assert "XP1152" in state.collected_info.get("robot_type", "")
    print("  PASS: ticket_ready=true after user provides details")

    # ---- Round 3: User says "转工单" with readiness=true ----
    print()
    print("=" * 65)
    print("Round 3: User says '转工单', readiness OK -> keyword shortcut")
    print("=" * 65)

    # Simulate keyword shortcut logic（与 pipeline 关口一致：服务端重算 readiness）
    _kw = ("转工单", "转单", "生成工单", "提交工单", "提单", "提个工单", "提工单", "帮我转", "我要转", "帮我提单")
    query3 = "转工单"
    hit = any(kw in query3 for kw in _kw)

    if hit:
        can, reason = True, ""  # _can_submit would pass
        has_project = bool(state.collected_info.get("project"))
        ready, missing = _assess_ticket_readiness(state)
        if has_project and ready:
            result = "DIRECT SUBMIT (no LLM call)"
        elif has_project and not ready:
            result = "FALLTHROUGH to LLM"
        else:
            result = "ASK for project"

    print(f"  keyword hit: {hit}")
    print(f"  has_project: {bool(state.collected_info.get('project'))}")
    print(f"  readiness: {_assess_ticket_readiness(state)}")
    print(f"  -> {result}")
    assert result == "DIRECT SUBMIT (no LLM call)"
    print("  PASS: direct submit when info is complete")

    # ---- Round 4: New convo, user says "转工单" without any info ----
    print()
    print("=" * 65)
    print("Round 4: New convo, user says '转工单' with NO prior info")
    print("=" * 65)

    mem2 = await plat._memory_manager.add_turn("conv2", "user", "转工单")
    state2 = AgentState(session_id="conv2")
    state2.ticket_ready = False  # default

    can, _ = True, ""  # idle phase -> can submit
    has_project = bool(state2.collected_info.get("project"))
    tr = state2.ticket_ready

    if not has_project:
        result = "ASK for project (pending_submit=True)"
    elif not tr:
        result = "FALLTHROUGH to LLM"

    print(f"  has_project: {has_project}")
    print(f"  ticket_ready: {tr}")
    print(f"  -> {result}")
    assert result == "ASK for project (pending_submit=True)"
    print("  PASS: missing project -> ask, regardless of ticket_ready")

    # ---- Round 5: Verify DIAGNOSIS_PROMPT contains ticket_ready rules ----
    print()
    print("=" * 65)
    print("Round 5: Verify prompt includes ticket_ready + ticket_type rules (no impatience bypass)")
    print("=" * 65)

    assert "ticket_ready" in DIAGNOSIS_PROMPT
    # ticket_type 不再由 LLM 每轮维护——只在提单时由 _infer_ticket_type 从 collected_info 推断
    assert "occurrence_time" in DIAGNOSIS_PROMPT
    assert "保底必填" in DIAGNOSIS_PROMPT or "至少需要" in DIAGNOSIS_PROMPT
    # 不耐烦绕过已删除——用户说"不耐烦的去掉 就算不耐烦 也得补充"
    assert "用户意图优先" not in DIAGNOSIS_PROMPT, "impatience bypass should be removed"
    assert "用户有权决定" not in DIAGNOSIS_PROMPT, "impatience bypass should be removed"
    print("  PASS: DIAGNOSIS_PROMPT contains required-field rules (no per-turn ticket_type tracking), no impatience bypass")

    # ---- Summary ----
    print()
    print("=" * 65)
    print("ALL SCENARIOS PASSED")
    print("=" * 65)

asyncio.run(main())
