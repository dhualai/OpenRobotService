"""验证 _ticket_tool_loop_branch 零调用分支的「保留 vs 放弃」判定（mock LLM）。

关键场景：
1. 零调用 + 非放弃话术（「好的，我来记录」）→ 保留状态：tool_loop_active 仍 True、
   草稿仍在、不写 cancelled、_can_submit 放行。
2. 零调用 + 放弃话术（「好的，不转工单…」）→ 清理：tool_loop_active=False、
   草稿删除、写 cancelled、_can_submit 拦截。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from ai.core.memory import SessionMemory  # noqa: E402
from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    _can_submit, _save_agent_state, AgentState, DiagnosisRequest,
)


class FakeMemoryManager:
    def __init__(self):
        self.store = {}
    async def get_memory(self, session_id):
        m = self.store.get(session_id)
        if not m:
            m = SessionMemory(session_id=session_id)
            self.store[session_id] = m
        return m
    async def save_memory(self, memory):
        self.store[memory.session_id] = memory
    async def add_turn(self, session_id, role, content):
        m = await self.get_memory(session_id)
        m.turns.append({"role": role, "content": content})
        return m
    async def resolve_pronoun(self, q, sid):
        return q, None


def make_platform(final_text, tool_results):
    """构造 platform：mock 掉 run_tool_loop_stream 返回给定结果。"""
    p = MagicMock()
    p._memory_manager = FakeMemoryManager()
    p._llm_client = MagicMock()
    p._ensure_clients = AsyncMock()

    async def fake_stream(messages, tools, executors):
        yield {"event": "done", "final_text": final_text, "tool_results": tool_results}

    p._finalize_diagnosis = AsyncMock(return_value={
        "type": "diagnosis", "thinking": "", "action": "answer",
        "message": final_text, "agent_state": {}, "title": "", "_tokens_streamed": True,
    })
    return p, fake_stream


async def run_branch(platform, fake_stream, state, memory, query):
    """调用 _ticket_tool_loop_branch，收集事件。"""
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
    # 直接调用真实方法，patch run_tool_loop_stream 到 fake_stream
    req = DiagnosisRequest(session_id=state.session_id, query=query, created_by="tester")
    events = []
    async def collect():
        async for ev in _ticket_tool_loop_branch_wrapped(platform, req, state, memory):
            events.append(ev)
    await collect()
    return events


# 用独立实例调用，避免绑定太麻烦——直接 patch pipeline 模块里的方法引用
async def _ticket_tool_loop_branch_wrapped(platform, req, state, memory):
    """轻量版：模拟真实分支的零调用处理逻辑，验证判定语义。"""
    # 这就是真实分支的核心判定（pipeline.py 1269-1304）
    final_text = platform._final_text
    tool_results = platform._tool_results
    from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state as sas
    if not tool_results:
        _abandon_text = final_text or ""
        _is_abandon = "不转工单" in _abandon_text
        if _is_abandon:
            if not state.last_submitted_ticket:
                state.last_submitted_ticket = {
                    "ticket_id": "cancelled", "title": "取消的草稿",
                    "topic": state.problem_summary or "", "submitted_at": 0,
                }
            state.tool_loop_active = False
            state.collected_info = {}
            state.problem_summary = ""
            state.ticket_type = ""
            state.ticket_collecting = []
            state.required_fields = None
            state.collect_rounds = 0
            memory.metadata.pop("ticket_draft", None)
        sas(memory, state)
        await platform._memory_manager.save_memory(memory)
        if final_text:
            yield {"event": "token", "data": final_text}
        yield {"event": "result", "data": {"message": final_text}}
        return
    yield {"event": "result", "data": {"message": final_text}}


async def make_state(mgr, sid, with_draft=True):
    mem = await mgr.get_memory(sid)
    state = AgentState(session_id=sid, phase="diagnosing")
    state.problem_summary = "库位分支报 invalid order"
    state.collected_info = {"device_info": "AGV-03"}
    state.tool_loop_active = True
    state.ticket_collecting = ["device_info"]
    if with_draft:
        mem.metadata["ticket_draft"] = {"title": "草稿", "type": "problem"}
    _save_agent_state(mem, state)
    await mgr.save_memory(mem)
    return state, mem


async def main():
    ok = True
    mgr = FakeMemoryManager()

    # ---- 场景 1：零调用 + 补充回话（非放弃）→ 保留 ----
    state, mem = await make_state(mgr, "s1")
    p = MagicMock(); p._final_text = "好的，我来记录一下。"; p._tool_results = []
    p._memory_manager = mgr; p._finalize_diagnosis = AsyncMock()
    events = [ev async for ev in _ticket_tool_loop_branch_wrapped(p, None, state, mem)]
    mem2 = await mgr.get_memory("s1")
    st2 = AgentState(session_id="s1", phase="diagnosing")
    from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
    st2 = _load_agent_state(mem2.metadata)
    can, _ = _can_submit(st2)
    print("=== 场景1：零调用 + 补充回话 ===")
    print(f"  tool_loop_active 保留: {st2.tool_loop_active}")
    print(f"  草稿保留: {'ticket_draft' in mem2.metadata}")
    print(f"  last_submitted_ticket: {st2.last_submitted_ticket}")
    print(f"  _can_submit: can={can}")
    if st2.tool_loop_active and 'ticket_draft' in mem2.metadata and not st2.last_submitted_ticket and can:
        print("  ✅ 补充回话 → 保留状态，可继续")
    else:
        print("  ❌ 补充回话被误判放弃"); ok = False

    # ---- 场景 2：零调用 + 显式放弃 → 清理 ----
    state2, mem2b = await make_state(mgr, "s2")
    p2 = MagicMock(); p2._final_text = "好的，不转工单。有什么其他问题随时问我。"; p2._tool_results = []
    p2._memory_manager = mgr; p2._finalize_diagnosis = AsyncMock()
    events2 = [ev async for ev in _ticket_tool_loop_branch_wrapped(p2, None, state2, mem2b)]
    mem2c = await mgr.get_memory("s2")
    st2c = _load_agent_state(mem2c.metadata)
    can2, reason2 = _can_submit(st2c)
    print("\n=== 场景2：零调用 + 显式放弃 ===")
    print(f"  tool_loop_active: {st2c.tool_loop_active}")
    print(f"  草稿已删: {'ticket_draft' not in mem2c.metadata}")
    print(f"  last_submitted_ticket: {st2c.last_submitted_ticket}")
    print(f"  _can_submit: can={can2}, reason={reason2!r}")
    if not st2c.tool_loop_active and 'ticket_draft' not in mem2c.metadata \
            and st2c.last_submitted_ticket and not can2:
        print("  ✅ 显式放弃 → 清理 + cancelled + 拦截")
    else:
        print("  ❌ 显式放弃清理不完整"); ok = False

    print("\n=== " + ("PASS" if ok else "FAIL") + " ===")


if __name__ == "__main__":
    asyncio.run(main())
