import asyncio, logging, sys
sys.path.insert(0, r"D:/Code/OpenRobotService")
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform, AgentState, DiagnosisRequest,
    _load_agent_state, _save_agent_state,
)

async def main():
    platform = AiDiagnosisPlatform()
    await platform._ensure_clients()
    await platform._retriever._ensure_clients()
    session_id = "repro_proj_gate_0916gf"
    text = "帮我提个工单，我们一台XQE车报错813停在货架前取不了货，车编号A027"
    memory = await platform._memory_manager.get_memory(session_id)
    state = _load_agent_state(memory.metadata)
    if state is None:
        state = AgentState(session_id=session_id, phase="idle",
                           original_query=text, problem_summary=text)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)
    request = DiagnosisRequest(session_id=session_id, query=text)
    stages, answer = [], []
    async for ev in platform._agent_think_stream(request, state, memory):
        if ev["event"] == "token":
            answer.append(ev["data"])
        elif ev["event"] == "status":
            stages.append(ev["data"].get("stage"))
    print("ANSWER:", "".join(answer)[:150])
    print("STAGES:", stages)

asyncio.run(main())
