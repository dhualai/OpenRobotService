"""取消提单场景验证：收集过程中说「不提单了」→ 状态清空恢复正常。

通过 run_in_copy.py 运行：
  python run_in_copy.py scripts/test_tool_loop_cancel.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.environ["AI_TICKET_TOOL_LOOP"] = "1"

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    DiagnosisRequest,
    get_diagnosis_platform,
)


async def run_round(platform, session, query, label):
    req = DiagnosisRequest(
        session_id=session, query=query, created_by="tester", skip_retrieval=True)
    stages, tokens = [], []
    async for ev in platform.run_stream(req):
        if ev.get("event") == "status":
            stages.append(ev.get("data", {}).get("stage"))
        elif ev.get("event") == "token":
            tokens.append(ev["data"])
    print(f"\n=== {label} ===")
    print("stages:", stages)
    print("回复:", "".join(tokens)[:150])


async def main():
    platform = await get_diagnosis_platform()
    session = "sess_cancel_001"

    # 第一轮：提单 → 收集模式
    await run_round(platform, session, "帮我提单，车不动了", "提单（进收集）")
    memory = await platform._memory_manager.get_memory(session)
    state = memory.metadata.get("agent_state", {})
    print("collecting状态:", bool(state.get("tool_loop_active")), "| collected:", state.get("collected_info", {}))

    # 第二轮：取消提单
    await run_round(platform, session, "算了不提单了", "取消提单")
    memory = await platform._memory_manager.get_memory(session)
    state = memory.metadata.get("agent_state", {})
    print("collecting状态:", bool(state.get("tool_loop_active")), "| collected:", state.get("collected_info", {}))

    # 断言：取消后状态清空
    assert not state.get("tool_loop_active"), "取消后 tool_loop_active 应清空"
    assert not state.get("collected_info"), "取消后 collected_info 应清空"
    print("\n=== ALL PASS ===")


if __name__ == "__main__":
    asyncio.run(main())
