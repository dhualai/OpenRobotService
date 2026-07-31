"""
Comprehensive conversation test for ticket_ready flow (v2: per-type required fields).
All keyword triggers use Chinese (matching _short_kw); fault descriptions use English/Chinese mix.

就绪判定（服务端 _assess_ticket_readiness，按 ticket_type 的保底必填清单）：
  problem: occurrence_time / robot_type(非泛称) / frequency
  feature: scenario / expected_effect

Covers:
  A: vague fault -> turn ticket -> no project -> ask project -> user gives project
     but info thin -> LLM asks -> user provides all -> submit
  B: multi-round diagnosis accumulates info -> ready -> turn ticket -> project -> direct submit
  C: submit -> immediately try again -> closed-loop block
  D: previous ticket done -> new problem -> diagnose -> submit
  E: info insufficient, user tries to force submit -> system refuses until info provided
  F: LLM sets ticket_ready=true but missing frequency -> server forces false -> fallthrough asks
  G: LLM action=submit but fields missing -> no ticket, deterministic ask (not "提单异常")
  H: feature type -> scenario/expected_effect required
  I: generic robot_type ("AGV") -> not ready
  J: prepare_ticket (button path) -> not ready -> code=1 no draft; ready -> draft_ready
"""
import sys, json, asyncio, time
sys.path.insert(0, ".")

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform, AgentState, DiagnosisRequest,
    _load_agent_state, _save_agent_state, _assess_ticket_readiness,
)


# ================================================================
# Mock infrastructure
# ================================================================

class MockMemory:
    def __init__(self, sid):
        self.session_id = sid
        self.turns = []
        self.metadata = {}

class MockMemoryManager:
    max_turns = 20
    def __init__(self):
        self._m = {}
    async def get_memory(self, sid):
        return self._m.setdefault(sid, MockMemory(sid))
    async def save_memory(self, mem):
        self._m[mem.session_id] = mem
    async def add_turn(self, sid, role, content):
        mem = await self.get_memory(sid)
        mem.turns.append({"role": role, "content": content})
        return mem
    async def add_pending_ticket(self, sid):
        pass
    async def resolve_pronoun(self, query, sid):
        return query, False

class MockLLM:
    _TICKET_JSON = (
        '{"type":"problem","title":"test ticket","description":"auto generated",'
        '"priority":"medium","contact":"","location":"N/A","robot_type":"unknown",'
        '"project":"unknown","fault_code":"","special_notes":"",'
        '"occurrence_time":"","frequency":""}'
    )
    def __init__(self):
        self.responses = []
        self.calls = []
    def set_responses(self, *responses):
        self.responses = list(responses)
    async def complete(self, prompt, **kw):
        self.calls.append(prompt[:200])
        # Auto-detect title generation (short prompt, returns a title string not JSON)
        if "生成一个简短标题" in prompt:
            return "Test Title"
        # Auto-detect _backfill_collected_info calls（提单关口回填，不消耗脚本队列）
        if "提取工单字段" in prompt:
            return "{}"
        # Auto-detect _build_ticket calls（不消耗脚本队列）
        if "生成结构化工单" in prompt:
            return self._TICKET_JSON
        if self.responses:
            return self.responses.pop(0)
        return '{"action":"answer","intent":"chat","state_update":{}}\n```\nOK.'

class MockRetriever:
    async def retrieve_domain(self, *a, **kw): return []
    async def retrieve_cheduan(self, *a, **kw): return []
    def _extract_error_codes(self, q): return []


# ================================================================
# Test runner
# ================================================================

def make_plat(memory_mgr, llm, retriever):
    plat = AiDiagnosisPlatform()
    plat._memory_manager = memory_mgr
    plat._llm_client = llm
    plat._retriever = retriever

    async def mock_submit(sid, created_by=""):
        mem = await plat._memory_manager.get_memory(sid)
        state = _load_agent_state(mem.metadata)
        if state is None: state = AgentState(session_id=sid)
        state.ticket_seq += 1
        state.phase = "resolved"
        state.last_submitted_ticket = {
            "ticket_id": f"AI-{sid[-6:]}-{int(time.time())%100000}",
            "db_id": state.ticket_seq, "title": "test ticket",
            "topic": state.problem_summary, "submitted_at": int(time.time()),
        }
        state.problem_summary = ""
        state.ruled_out = []
        state.hypotheses = []
        state.collected_info = {}
        state.diagnosis_rounds = 0
        state.original_query = ""
        state.ticket_ready = False
        state.ticket_type = ""
        _save_agent_state(mem, state)
        await plat._memory_manager.save_memory(mem)
        return {
            "type": "ticket",
            "data": {
                "ticket": {"ticket_id": state.last_submitted_ticket["ticket_id"], "title": "test"},
                "db_id": state.ticket_seq, "notice": "ok",
            },
        }
    plat.submit = mock_submit
    return plat


def step_ok(i, query, phase, exp_ph, tr, pj, ps, has_tk, exp_tk, msg):
    ph_ok = phase == exp_ph
    tk_ok = has_tk == exp_tk
    ok = ph_ok and tk_ok
    status = "PASS" if ok else "FAIL"
    print(f"  R{i}: '{query[:35]}'")
    print(f"    phase={phase}(exp:{exp_ph}) tr={tr} pj='{pj}' ps='{ps[:30]}' "
          f"ticket={'Y' if has_tk else 'N'}(exp:{'Y' if exp_tk else 'N'}) [{status}]")
    if msg: print(f"    msg: {msg[:60]}")
    if not ph_ok: print(f"    !! phase: got {phase}, expected {exp_ph}")
    if not tk_ok: print(f"    !! ticket: got {'Y' if has_tk else 'N'}, expected {'Y' if exp_tk else 'N'}")
    return ok


async def run_steps(plat, mm, sid, steps):
    """逐步跑对话，返回 (all_pass, last_result)"""
    all_pass = True
    last = None
    for i, (q, exp_ph, exp_tk) in enumerate(steps, 1):
        req = DiagnosisRequest(session_id=sid, query=q)
        mem = await mm.get_memory(sid)
        state = _load_agent_state(mem.metadata) or AgentState(session_id=sid)
        result = await plat._agent_think(req, state, mem)
        last = result
        state = _load_agent_state(mem.metadata) or state
        has_tk = "ticket" in result
        if not step_ok(i, q, state.phase, exp_ph, state.ticket_ready,
                       state.collected_info.get("project", ""),
                       state.problem_summary, has_tk, exp_tk, result.get("message", "")):
            all_pass = False
    return all_pass, last


async def main():
    ret = MockRetriever()
    all_pass = True

    # ================================================================
    # A: vague fault -> turn ticket -> no project -> ask project ->
    #    user gives project but info thin -> LLM asks -> user provides all -> submit
    # ================================================================
    llm_a = MockLLM()
    llm_a.set_responses(
        # R1: vague fault -> LLM: ticket_ready=false, asks
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"Vehicle cannot move","ticket_type":"problem","ticket_ready":false,"collected_info":{}}}\n```\n什么时候开始的？什么车型？每次还是偶尔？',
        # R3: pending_submit fallthrough -> project recorded but info thin -> LLM asks
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"Vehicle cannot move","ticket_type":"problem","ticket_ready":false,"collected_info":{}}}\n```\n项目记下了。故障什么时候开始的？车型？频率？',
        # R4: user provides all required -> LLM: ticket_ready=true + action=submit
        '```json\n{"action":"submit","intent":"troubleshoot","state_update":{"problem_summary":"XP1152 cannot move since yesterday","ticket_type":"problem","ticket_ready":true,"collected_info":{"robot_type":"XP1152","occurrence_time":"昨天下午","frequency":"每次"}}}\n```\n好的',
    )
    mm_a = MockMemoryManager()
    plat_a = make_plat(mm_a, llm_a, ret)

    print("=" * 65)
    print("  A: vague fault -> turn ticket -> collect project+details -> submit")
    print("=" * 65)
    ok, _ = await run_steps(plat_a, mm_a, "sessA", [
        ("Vehicle not moving", "diagnosing", False),
        ("转工单", "diagnosing", False),            # no project -> ask project
        ("Shanghai Warehouse", "diagnosing", False), # project given, info thin -> LLM asks
        ("型号XP1152，昨天下午开始，每次都这样", "resolved", True),  # all fields -> submit
    ])
    all_pass = all_pass and ok

    # ================================================================
    # B: multi-round diagnosis -> ready -> turn ticket -> project -> direct submit
    # ================================================================
    llm_b = MockLLM()
    llm_b.set_responses(
        # R1: fault report -> LLM: ticket_ready=false
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"XP1152 not moving","ticket_type":"problem","ticket_ready":false,"collected_info":{"robot_type":"XP1152"}}}\n```\n什么时候开始的？每次都这样还是偶尔？',
        # R2: time + frequency -> LLM: ticket_ready=true
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"XP1152 not moving since yesterday","ticket_type":"problem","ticket_ready":true,"collected_info":{"robot_type":"XP1152","occurrence_time":"昨天下午","frequency":"每次"}}}\n```\n先检查电机供电，不行就转工单。',
    )
    mm_b = MockMemoryManager()
    plat_b = make_plat(mm_b, llm_b, ret)

    print()
    print("=" * 65)
    print("  B: diagnosis -> ready -> collect project -> direct submit")
    print("=" * 65)
    ok, _ = await run_steps(plat_b, mm_b, "sessB", [
        ("XP1152 not moving", "diagnosing", False),
        ("昨天下午开始，每次都这样", "diagnosing", False),
        ("转工单", "diagnosing", False),       # no project -> ask project
        ("Shanghai Warehouse", "resolved", True),  # project + ready -> direct submit
    ])
    all_pass = all_pass and ok

    # ================================================================
    # C: submit -> immediately try again -> blocked
    # ================================================================
    llm_c = MockLLM()
    mm_c = MockMemoryManager()
    plat_c = make_plat(mm_c, llm_c, ret)
    mem_c = await mm_c.get_memory("sessC")
    state_c = AgentState(session_id="sessC")
    state_c.problem_summary = "Robot fault at Shanghai"
    state_c.ticket_type = "problem"
    state_c.collected_info = {
        "project": "Shanghai Warehouse", "robot_type": "XP1152",
        "occurrence_time": "昨天", "frequency": "每次",
    }
    state_c.ticket_ready = True
    _save_agent_state(mem_c, state_c)
    await mm_c.save_memory(mem_c)

    print()
    print("=" * 65)
    print("  C: submit -> try again -> blocked")
    print("=" * 65)
    ok, _ = await run_steps(plat_c, mm_c, "sessC", [
        ("转工单", "resolved", True),   # ready + project -> submit
        ("转工单", "resolved", False),  # blocked (no new problem)
    ])
    all_pass = all_pass and ok

    # ================================================================
    # D: prev ticket done -> new problem -> diagnose -> submit
    # ================================================================
    llm_d = MockLLM()
    llm_d.set_responses(
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"AGV offline at Shenzhen","ticket_type":"problem","ticket_ready":false,"collected_info":{}}}\n```\n什么时候离线的？车型？频率？',
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"AGV X2 offline since morning","ticket_type":"problem","ticket_ready":true,"collected_info":{"robot_type":"AGV-X2","occurrence_time":"今天上午","frequency":"首次"}}}\n```\n先查网络，不行转工单。',
    )
    mm_d = MockMemoryManager()
    plat_d = make_plat(mm_d, llm_d, ret)
    mem_d = await mm_d.get_memory("sessD")
    state_d = AgentState(session_id="sessD")
    state_d.phase = "resolved"
    state_d.collected_info = {"project": "Shenzhen Factory"}
    state_d.last_submitted_ticket = {
        "ticket_id": "AI-prev-123", "db_id": 1, "title": "old",
        "topic": "old problem", "submitted_at": int(time.time()) - 60,
    }
    _save_agent_state(mem_d, state_d)
    await mm_d.save_memory(mem_d)

    print()
    print("=" * 65)
    print("  D: prev ticket done -> new problem -> diagnose -> submit")
    print("=" * 65)
    ok, _ = await run_steps(plat_d, mm_d, "sessD", [
        ("AGV offline again", "diagnosing", False),
        ("型号AGV-X2，今天上午开始，第一次出现", "diagnosing", False),
        ("转工单", "resolved", True),          # ready + project -> submit
    ])
    all_pass = all_pass and ok

    # ================================================================
    # E: thin info -> user insists -> still refused -> provides info -> submit
    # ================================================================
    llm_e = MockLLM()
    llm_e.set_responses(
        # R1: keyword hit, project exists, not ready -> fallthrough -> LLM asks
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"robot issue","ticket_type":"problem","ticket_ready":false,"collected_info":{}}}\n```\n故障什么时候开始的？什么车型？每次还是偶尔？',
        # R2: user impatient but LLM still refuses (no impatience bypass)
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"robot issue","ticket_type":"problem","ticket_ready":false,"collected_info":{}}}\n```\n理解你着急，但没有这些信息工程师没法处理。发生时间？车型？频率？',
        # R3: user finally provides all -> LLM: ticket_ready=true + action=submit
        '```json\n{"action":"submit","intent":"troubleshoot","state_update":{"problem_summary":"XP1152 error at Beijing WH","ticket_type":"problem","ticket_ready":true,"collected_info":{"robot_type":"XP1152","occurrence_time":"昨天","frequency":"每次"}}}\n```\n好的',
    )
    mm_e = MockMemoryManager()
    plat_e = make_plat(mm_e, llm_e, ret)
    mem_e = await mm_e.get_memory("sessE")
    state_e = AgentState(session_id="sessE")
    state_e.problem_summary = "robot issue"
    state_e.collected_info = {"project": "Beijing Warehouse"}
    state_e.ticket_ready = False
    _save_agent_state(mem_e, state_e)
    await mm_e.save_memory(mem_e)

    print()
    print("=" * 65)
    print("  E: thin info -> user insists -> still refused -> provides info -> submit")
    print("=" * 65)
    ok, _ = await run_steps(plat_e, mm_e, "sessE", [
        ("转工单", "diagnosing", False),                       # not ready -> fallthrough -> LLM asks
        ("Stop asking, just submit it", "diagnosing", False),   # impatient -> LLM STILL refuses
        ("型号XP1152，昨天开始，每次都这样", "resolved", True),  # provides all -> submit
    ])
    all_pass = all_pass and ok

    # ================================================================
    # F: LLM sets ticket_ready=true but missing frequency ->
    #    server hard check forces false -> 转工单 falls through -> LLM asks
    # ================================================================
    llm_f = MockLLM()
    llm_f.set_responses(
        # R1: LLM wrongly claims ready (missing frequency) -> server forces false
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"XP1152 not moving","ticket_type":"problem","ticket_ready":true,"collected_info":{"robot_type":"XP1152","occurrence_time":"昨天"}}}\n```\n是每次都这样还是偶尔？',
        # R2: keyword fallthrough -> LLM asks for the missing field
        '```json\n{"action":"ask","intent":"troubleshoot","state_update":{"problem_summary":"XP1152 not moving","ticket_type":"problem","ticket_ready":false,"collected_info":{}}}\n```\n还差一点：故障是每次出现还是偶尔？',
    )
    mm_f = MockMemoryManager()
    plat_f = make_plat(mm_f, llm_f, ret)
    mem_f = await mm_f.get_memory("sessF")
    state_f = AgentState(session_id="sessF")
    state_f.problem_summary = "XP1152 not moving"
    state_f.collected_info = {"project": "Shanghai Warehouse"}
    _save_agent_state(mem_f, state_f)
    await mm_f.save_memory(mem_f)

    print()
    print("=" * 65)
    print("  F: LLM 虚报 ticket_ready=true (缺 frequency) -> 服务端打回 -> 追问")
    print("=" * 65)
    ok, last_f = await run_steps(plat_f, mm_f, "sessF", [
        ("XP1152 not moving", "diagnosing", False),   # tr=true 被打回 -> 无 ticket
        ("转工单", "diagnosing", False),               # readiness false -> fallthrough -> asks
    ])
    # 验证打回确实发生：R1 后 state.ticket_ready 必须为 False
    mem_f2 = await mm_f.get_memory("sessF")
    st_f2 = _load_agent_state(mem_f2.metadata)
    if st_f2.ticket_ready:
        print("    !! FAIL: ticket_ready should be forced False by server hard check")
        ok = False
    else:
        print("    PASS: server forced ticket_ready=False (missing frequency)")
    all_pass = all_pass and ok

    # ================================================================
    # G: LLM disobedient (action=submit with fields missing) ->
    #    no ticket, deterministic ask message (NOT "提单异常")
    # ================================================================
    llm_g = MockLLM()
    llm_g.set_responses(
        # R1: keyword fallthrough -> LLM misbehaves: submit with nothing collected
        '```json\n{"action":"submit","intent":"troubleshoot","state_update":{"problem_summary":"robot issue","ticket_type":"problem","ticket_ready":true,"collected_info":{}}}\n```\n好的',
    )
    mm_g = MockMemoryManager()
    plat_g = make_plat(mm_g, llm_g, ret)
    mem_g = await mm_g.get_memory("sessG")
    state_g = AgentState(session_id="sessG")
    state_g.problem_summary = "robot issue"
    state_g.collected_info = {"project": "Beijing Warehouse"}
    _save_agent_state(mem_g, state_g)
    await mm_g.save_memory(mem_g)

    print()
    print("=" * 65)
    print("  G: LLM 不听话 (submit 但字段缺失) -> 拦截 + 确定性追问")
    print("=" * 65)
    ok, last_g = await run_steps(plat_g, mm_g, "sessG", [
        ("转工单", "diagnosing", False),   # no ticket; action converted to ask
    ])
    msg_g = (last_g or {}).get("message", "")
    if "补上" not in msg_g and "还差" not in msg_g:
        print(f"    !! FAIL: expected deterministic ask containing '还差', got: {msg_g[:60]}")
        ok = False
    elif "提单过程中出现异常" in msg_g:
        print(f"    !! FAIL: got technical error message instead of ask: {msg_g[:60]}")
        ok = False
    else:
        print(f"    PASS: deterministic ask message (no ticket, no error msg)")
    if (last_g or {}).get("action") != "ask":
        print(f"    !! FAIL: action should be 'ask', got {(last_g or {}).get('action')}")
        ok = False
    all_pass = all_pass and ok

    # ================================================================
    # H: feature type -> scenario/expected_effect required
    # ================================================================
    llm_h = MockLLM()
    llm_h.set_responses(
        '```json\n{"action":"submit","intent":"howto","state_update":{"problem_summary":"需要批量删除站点功能","ticket_type":"feature","ticket_ready":true,"collected_info":{"scenario":"地图编辑站点过多需批量清理","expected_effect":"批量删除提升效率"}}}\n```\n好的',
    )
    mm_h = MockMemoryManager()
    plat_h = make_plat(mm_h, llm_h, ret)
    mem_h = await mm_h.get_memory("sessH")
    state_h = AgentState(session_id="sessH")
    state_h.problem_summary = "需要一个新功能"
    state_h.collected_info = {"project": "Shanghai Warehouse"}
    _save_agent_state(mem_h, state_h)
    await mm_h.save_memory(mem_h)

    print()
    print("=" * 65)
    print("  H: feature 类型 -> scenario/expected_effect 齐全 -> submit")
    print("=" * 65)
    ok, _ = await run_steps(plat_h, mm_h, "sessH", [
        ("我想要地图上能批量删除站点，站点太多了效率太低", "resolved", True),
    ])
    # feature 缺 expected_effect -> not ready（纯判定单测）
    s_h = AgentState(session_id="unitH")
    s_h.ticket_type = "feature"
    s_h.collected_info = {"scenario": "站点太多"}
    ready_h, missing_h = _assess_ticket_readiness(s_h)
    if ready_h or not any("期望效果" in m for m in missing_h):
        print(f"    !! FAIL: feature missing expected_effect should be not ready, got ready={ready_h} missing={missing_h}")
        ok = False
    else:
        print(f"    PASS: feature 缺 expected_effect -> not ready ({missing_h})")
    all_pass = all_pass and ok

    # ================================================================
    # I: generic robot_type -> not ready
    # ================================================================
    print()
    print("=" * 65)
    print("  I: 泛化车型判定单测")
    print("=" * 65)
    for model, expect_ready in [("AGV", False), ("机器人", False), ("小车", False),
                                 ("XP1152", True), ("蚂蚁X1", True)]:
        s_i = AgentState(session_id="unitI")
        s_i.ticket_type = "problem"
        s_i.collected_info = {"robot_type": model, "occurrence_time": "昨天", "frequency": "每次"}
        ready_i, missing_i = _assess_ticket_readiness(s_i)
        status = "PASS" if ready_i == expect_ready else "FAIL"
        if ready_i != expect_ready:
            all_pass = False
        print(f"  {status} | robot_type='{model}' -> ready={ready_i} (exp:{expect_ready}) {missing_i if not ready_i else ''}")
    # 未判定类型：从 collected_info 推断。有 feature 字段 → 推断为 feature，按 feature 清单判定
    s_i2 = AgentState(session_id="unitI2")
    s_i2.collected_info = {"scenario": "x", "expected_effect": "y"}
    ready_i2, missing_i2 = _assess_ticket_readiness(s_i2)
    if not ready_i2:
        print(f"  FAIL | scenario+expected_effect 应推断为 feature 且就绪, got missing={missing_i2}")
        all_pass = False
    else:
        print(f"  PASS | scenario+expected_effect → 推断 feature → ready")
    # 无任何信息 → 推断为 problem，按 problem 清单（最严）→ not ready
    s_i3 = AgentState(session_id="unitI3")
    ready_i3, missing_i3 = _assess_ticket_readiness(s_i3)
    if ready_i3:
        print(f"  FAIL | 空信息应推断为 problem 且不就绪, got ready=True")
        all_pass = False
    else:
        print(f"  PASS | 空信息 → 推断 problem → not ready ({missing_i3})")

    # ================================================================
    # J: prepare_ticket (button path) -> not ready -> code=1 no draft;
    #    ready -> draft_ready
    # ================================================================
    llm_j = MockLLM()
    mm_j = MockMemoryManager()
    plat_j = make_plat(mm_j, llm_j, ret)

    print()
    print("=" * 65)
    print("  J: 按钮路径 prepare_ticket 信息不足拦截")
    print("=" * 65)
    # J1: thin state -> not_ready, no draft stored
    mem_j = await mm_j.get_memory("sessJ")
    state_j = AgentState(session_id="sessJ")
    state_j.problem_summary = "车不动了"
    _save_agent_state(mem_j, state_j)
    await mm_j.save_memory(mem_j)
    res_j1 = await plat_j.prepare_ticket("sessJ")
    j1_ok = (res_j1.get("code") == 1 and res_j1.get("stage") == "not_ready"
             and res_j1.get("missing_info") and mem_j.metadata.get("ticket_draft") is None)
    print(f"  {'PASS' if j1_ok else 'FAIL'} | 信息不足 -> code={res_j1.get('code')}, "
          f"stage={res_j1.get('stage')}, missing={res_j1.get('missing_info')}, "
          f"draft={'存在(不应存在!)' if mem_j.metadata.get('ticket_draft') else '无'}")
    all_pass = all_pass and j1_ok

    # J2: full state -> draft_ready
    state_j2 = AgentState(session_id="sessJ")
    state_j2.problem_summary = "XP1152 cannot move"
    state_j2.ticket_type = "problem"
    state_j2.collected_info = {
        "project": "Shanghai Warehouse", "robot_type": "XP1152",
        "occurrence_time": "昨天", "frequency": "每次",
    }
    _save_agent_state(mem_j, state_j2)
    await mm_j.save_memory(mem_j)
    res_j2 = await plat_j.prepare_ticket("sessJ")
    j2_ok = (res_j2.get("stage") == "draft_ready" and res_j2.get("draft")
             and mem_j.metadata.get("ticket_draft") is not None)
    print(f"  {'PASS' if j2_ok else 'FAIL'} | 信息齐全 -> stage={res_j2.get('stage')}, "
          f"draft={'有' if res_j2.get('draft') else '无'}")
    all_pass = all_pass and j2_ok

    print()
    print("=" * 65)
    if all_pass: print("  ALL SCENARIOS PASSED")
    else:        print("  SOME SCENARIOS FAILED - see above")
    print("=" * 65)


asyncio.run(main())
