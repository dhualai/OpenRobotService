"""上一单字段指代解析回归（真实入口 run_stream）。

    python ai/tests/verify_prev_ticket_reference.py

需求：用户在下一单收集时说「车型还是上次提单的车型」要能解析成上一单的真值。
设计（无固定字段清单，判断全交 LLM）：
  - 提单收尾把上一单 collected_info（LLM 动态字段+用户原话）+ 最终 description
    存进 last_submitted_ticket
  - 收集轮 / 提单快路径注入「上一单记录」块：仅解析明确指代，禁止主动带入，
    禁止把指代原文当字段值
  - _build_ticket 不注入 → 草稿生成物理看不到上一单，串单通道堵死

S1 正向：指代「车型还是上次提单的车型」→ collected_info 解析出 3号车，
        绝不落「上次/一样/还是」指代原文
S2 负向：全程不提上一单 → 新单草稿不含上一单的 3号车
"""
import asyncio, io, logging, os, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s",
                    stream=sys.stderr)

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))


PREV_TICKET = {
    "ticket_id": "T-100",
    "db_id": 100,
    "title": "任务配置报错",
    "topic": "任务配置报错",
    "submitted_at": 1755945600,
    "collected_info": {"robot_id": "3号车", "schedule_version": "2.6.4"},
    "description": "3号车任务配置后发任务报错，调度版本2.6.4，已重新下发配置恢复。",
}

PRELOAD = [
    {"role": "user", "content": "又出问题了，发任务没反应"},
    {"role": "assistant", "content": "好的，帮你记录。请问是哪台车出的问题？"},
]


async def run_turn(agent, sid, query):
    from ai.agents.AiDiagnosisPlatform.pipeline import DiagnosisRequest
    stages, text = [], []
    async for ev in agent.run_stream(DiagnosisRequest(session_id=sid, query=query)):
        if ev.get("event") == "status":
            stages.append((ev["data"] or {}).get("stage", ""))
        elif ev.get("event") == "token":
            text.append(ev.get("data") or "")
    return stages, "".join(text)


async def setup(agent, sid, collecting):
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState, _save_agent_state
    memory = await agent._memory_manager.get_memory(sid)
    memory.turns = [dict(t) for t in PRELOAD]
    st = AgentState(session_id=sid, phase="diagnosing",
                    original_query="又出问题了，发任务没反应",
                    problem_summary="发任务没反应",
                    ticket_type="problem",
                    required_fields={"robot_id": "车辆编号", "error_detail": "报错详情"},
                    ticket_collecting=list(collecting),
                    last_submitted_ticket=dict(PREV_TICKET))
    _save_agent_state(memory, st)
    await agent._memory_manager.save_memory(memory)


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform as AgentCls

    agent = AgentCls()
    await agent._ensure_clients()
    await agent._retriever.retrieve_domain_dual("预热", "team", top_k=1)

    # ── S1 正向：指代解析 ──
    print("\nS1 车型还是上次提单的车型")
    sid = "verify_prev_ref"
    await setup(agent, sid, ["车辆编号", "报错详情"])
    stages, reply = await run_turn(agent, sid, "车型还是上次提单的车型")
    print(f"    stages={stages}")
    print(f"    reply={reply[:120]}")
    memory = await agent._memory_manager.get_memory(sid)
    ci = memory.metadata.get("agent_state", {}).get("collected_info") or {}
    print(f"    collected_info={ci}")
    robot_val = str(ci.get("robot_id") or "")
    check("S1 指代解析出上一单真值", "3号" in robot_val, f"robot_id={robot_val!r}")
    check("S1 未把指代原文当字段值",
          not any(w in robot_val for w in ("上次", "上一次", "一样", "还是")),
          f"robot_id={robot_val!r}")

    # ── S2 负向：全程不提上一单，新单草稿不得串入 3号车 ──
    print("\nS2 新单全程不指代上一单")
    sid2 = "verify_prev_noleak"
    await setup(agent, sid2, ["车辆编号", "报错详情"])
    stages, reply = await run_turn(agent, sid2, "7号车")
    print(f"    stages={stages}")
    print(f"    reply={reply[:100]}")
    if "review" not in stages:
        stages, reply = await run_turn(agent, sid2, "就是没反应，没有报错弹窗")
        print(f"    stages={stages}")
        print(f"    reply={reply[:100]}")
    memory2 = await agent._memory_manager.get_memory(sid2)
    draft = memory2.metadata.get("ticket_draft") or {}
    desc = str(draft.get("description") or "")
    print(f"    desc={desc[:180]}")
    check("S2 新单草稿已生成", bool(draft), "未生成草稿")
    check("S2 草稿含本单车辆 7号车", "7号" in desc, desc[:120])
    check("S2 草稿未串入上一单 3号车", "3号车" not in desc, desc[:120])

    print(f"\n═══ 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ═══")
    if FAIL:
        for f_ in FAIL:
            print("  失败:", f_)
        sys.exit(1)


asyncio.run(main())
