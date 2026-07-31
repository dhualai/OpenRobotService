"""Debug Session E: keyword shortcut with pre-populated project"""
import sys, asyncio
sys.path.insert(0, ".")

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform, AgentState, DiagnosisRequest,
    _load_agent_state, _save_agent_state,
)

class MockMemory:
    def __init__(self, sid): self.session_id = sid; self.turns = []; self.metadata = {}

class MockMM:
    max_turns = 20
    def __init__(self): self._m = {}
    async def get_memory(self, sid): return self._m.setdefault(sid, MockMemory(sid))
    async def save_memory(self, mem): self._m[mem.session_id] = mem
    async def add_turn(self, sid, role, content):
        mem = await self.get_memory(sid)
        mem.turns.append({"role": role, "content": content})
        return mem
    async def add_pending_ticket(self, sid): pass
    async def resolve_pronoun(self, q, sid): return q, False

class MockLLM:
    async def complete(self, prompt, **kw):
        if "生成结构化工单" in prompt:
            return '{"type":"problem","title":"t","description":"d","priority":"中","contact":"","location":"","robot_type":"","project":"","fault_code":"","special_notes":""}'
        return '{"action":"answer","intent":"chat","state_update":{}}\n```\nOK.'

class MockRet:
    async def retrieve_domain(self, *a, **kw): return []
    async def retrieve_cheduan(self, *a, **kw): return []
    def _extract_error_codes(self, q): return []

async def test():
    plat = AiDiagnosisPlatform()
    plat._memory_manager = MockMM()
    plat._llm_client = MockLLM()
    plat._retriever = MockRet()

    async def ms(sid, created_by=""):
        mem = await plat._memory_manager.get_memory(sid)
        s = _load_agent_state(mem.metadata) or AgentState(session_id=sid)
        s.ticket_seq += 1; s.phase = "resolved"
        s.last_submitted_ticket = {"ticket_id": "AI-xxx", "db_id": s.ticket_seq, "title": "t",
                                     "topic": s.problem_summary, "submitted_at": 0}
        s.problem_summary = ""; s.ruled_out = []; s.hypotheses = []; s.collected_info = {}
        s.diagnosis_rounds = 0; s.original_query = ""; s.ticket_ready = False
        _save_agent_state(mem, s); await plat._memory_manager.save_memory(mem)
        return {"type": "ticket", "data": {"ticket": {"ticket_id": "AI-xxx", "title": "t"},
                                             "db_id": s.ticket_seq, "notice": "ok"}}
    plat.submit = ms

    # Pre-populate state with project
    mem = await plat._memory_manager.get_memory("test")
    s = AgentState(session_id="test")
    s.collected_info = {"project": "Beijing WH"}
    s.ticket_ready = False
    _save_agent_state(mem, s)
    await plat._memory_manager.save_memory(mem)

    # Read back
    mem2 = await plat._memory_manager.get_memory("test")
    s2 = _load_agent_state(mem2.metadata)
    print("Before:", s2.collected_info, "tr:", s2.ticket_ready)
    assert s2.collected_info.get("project") == "Beijing WH", "FAIL: project not loaded!"

    # Call _agent_think
    req = DiagnosisRequest(session_id="test", query="帮我转工单")
    mem3 = await plat._memory_manager.get_memory("test")
    state3 = _load_agent_state(mem3.metadata) or AgentState(session_id="test")
    print("State for _agent_think:", state3.collected_info, "tr:", state3.ticket_ready)

    result = await plat._agent_think(req, state3, mem3)
    print("Message:", result.get("message", "")[:100])
    print("Phase:", result.get("agent_state", {}).get("phase"))
    print("ticket_ready:", result.get("agent_state", {}).get("ticket_ready"))

    # After
    mem4 = await plat._memory_manager.get_memory("test")
    s4 = _load_agent_state(mem4.metadata)
    print("After:", s4.collected_info, "tr:", s4.ticket_ready, "phase:", s4.phase,
          "pending_submit:", s4.pending_submit)

asyncio.run(test())
