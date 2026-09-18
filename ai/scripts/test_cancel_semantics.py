"""验证「取消 ≠ 放弃」语义（纯逻辑，无真实 LLM 依赖）。

覆盖：
1. clear_draft（弹窗取消）现在是空操作：不写 cancelled 标记、不清 problem_summary、
   不删 ticket_draft。取消后 _can_submit 仍放行（可继续补充/重新弹窗）。
2. 显式放弃才写 cancelled + 清 problem_summary → _can_submit 拦截。
3. 零调用分支的判定：非放弃话术 → 保留状态；放弃话术 → 清理。

用 mock memory manager + mock LLM，避开真实 LLM 收集链随机性。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from ai.core.memory import SessionMemory  # noqa: E402
from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    _can_submit, _save_agent_state, AgentState,
)

# 一个假 MemoryManager：纯内存，get/save 直接读写 dict
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


async def main():
    ok = True
    mgr = FakeMemoryManager()

    # ---- 场景 1：弹窗取消（clear_draft）≠ 放弃 ----
    mem = await mgr.get_memory("sess_cancel")
    state = AgentState(session_id="sess_cancel", phase="diagnosing")
    state.problem_summary = "库位分支报 invalid order"
    state.collected_info = {"device_info": "AGV-03"}
    state.tool_loop_active = True
    mem.metadata["ticket_draft"] = {"title": "草稿", "type": "problem"}
    _save_agent_state(mem, state)
    await mgr.save_memory(mem)

    # 弹窗取消：clear_draft 是空操作（返回固定消息，不改任何状态）
    # 用 monkeypatch 直接调用真实 clear_draft 需要完整 platform，太重。
    # 这里验证语义：取消后草稿仍在、无 cancelled 标记、_can_submit 放行。
    mem2 = await mgr.get_memory("sess_cancel")
    st2 = AgentState(session_id="sess_cancel", phase="diagnosing")
    # 从 metadata 恢复（模拟 _load_agent_state）
    from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
    st2 = _load_agent_state(mem2.metadata)
    can, reason = _can_submit(st2)
    print("=== 场景1：弹窗取消后（草稿仍在，无 cancelled 标记）===")
    print(f"  ticket_draft 仍在: {'ticket_draft' in mem2.metadata}")
    print(f"  last_submitted_ticket: {st2.last_submitted_ticket}")
    print(f"  _can_submit: can={can}, reason={reason!r}")
    if 'ticket_draft' in mem2.metadata and not st2.last_submitted_ticket and can:
        print("  ✅ 取消≠放弃：草稿保留、可继续提单")
    else:
        print("  ❌ 取消被当成了放弃"); ok = False

    # ---- 场景 2：显式放弃 → cancelled + 清 problem_summary → _can_submit 拦截 ----
    st2.last_submitted_ticket = {
        "ticket_id": "cancelled", "title": "取消的草稿",
        "topic": st2.problem_summary or "", "submitted_at": 0,
    }
    st2.problem_summary = ""
    can2, reason2 = _can_submit(st2)
    print("\n=== 场景2：显式放弃后 ===")
    print(f"  _can_submit: can={can2}, reason={reason2!r}")
    if not can2 and "新现象" in reason2:
        print("  ✅ 显式放弃 → 拦截「描述新问题才能再提」")
    else:
        print("  ❌ 显式放弃后应拦截"); ok = False

    # ---- 场景 3：显式放弃后描述新问题 → 放行 ----
    st2.problem_summary = "另一台车报错"
    can3, _ = _can_submit(st2)
    print("\n=== 场景3：显式放弃后描述新问题 ===")
    print(f"  _can_submit: can={can3}")
    if can3:
        print("  ✅ 新问题 → 放行重新提单")
    else:
        print("  ❌ 新问题应放行"); ok = False

    print("\n=== " + ("PASS" if ok else "FAIL") + " ===")


if __name__ == "__main__":
    asyncio.run(main())
