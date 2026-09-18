"""「谈论提单 ≠ 要求提单」回归（真实入口 run_stream + 真实意图分类）。

    python ai/tests/verify_talk_about_ticket.py

0824 生产事故：用户吐槽「提单找不到项目和领导」→ AI 追问时间 → 用户答
「今天提单出现的」→ 被判提单意图 → 突然弹工单草稿。修复后要求：
  - 意图分类：谈论提单功能问题/承接回答 → diagnosis；真实提单诉求仍 → ticket
  - 完整链路：重放该对话，run_stream 不得产出草稿（stage=review/generating_ticket）
"""
import asyncio, io, os, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))


CTX = [
    {"role": "user", "content": "新问题 突然给我提单我都懵逼了 你们这个系统有bug啊"},
    {"role": "assistant", "content": "明白了，你是说「摇人吧」这个服务号本身用着有问题？"},
    {"role": "user", "content": "提单找不到项目和领导啊"},
    {"role": "assistant", "content": "提单时找不到项目和领导，这个情况大概是什么时候开始的？"},
]


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        AiDiagnosisPlatform as AgentCls, DiagnosisRequest, _save_agent_state)
    from ai.core.llm import get_intent_client

    agent = AgentCls()
    await agent._ensure_clients()
    await agent._retriever.retrieve_domain_dual("预热", "team", top_k=1)
    _intent_llm = await get_intent_client()

    # ── S1 意图分类：生产原话 ──
    print("\nS1 意图分类（真实分类器）")
    for q, expect in [
        ("提单找不到项目和领导啊", "diagnosis"),
        ("今天提单出现的", "diagnosis"),
        ("新问题 突然给我提单我都懵逼了 你们这个系统有bug啊", "diagnosis"),
        ("转工单吧，这个问题现场解决不了", "ticket"),
        ("帮我提个单，车一直报错2101", "ticket"),
    ]:
        got = await agent._classify_intent(_intent_llm, q, "", context_turns=CTX)
        check(f"S1 {q[:18]!r} → {expect}", got == expect, f"got={got}")

    # ── S2 完整链路：重放事故对话最后一轮，不得弹草稿 ──
    print("\nS2 run_stream 重放：今天提单出现的")
    sid = "verify_talk_ticket"
    memory = await agent._memory_manager.get_memory(sid)
    memory.turns = [dict(t) for t in CTX]
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState
    agent_state = AgentState(session_id=sid, phase="diagnosing",
                             original_query="提单找不到项目和领导啊",
                             problem_summary="提单找不到项目和领导")
    _save_agent_state(memory, agent_state)
    await agent._memory_manager.save_memory(memory)

    stages, text = [], []
    async for ev in agent.run_stream(DiagnosisRequest(session_id=sid, query="今天提单出现的")):
        if ev.get("event") == "status":
            stages.append((ev["data"] or {}).get("stage", ""))
        elif ev.get("event") == "token":
            text.append(ev.get("data") or "")
    reply = "".join(text)
    print(f"    stages={stages}")
    print(f"    reply={reply[:150]}")
    check("S2 未触发草稿弹窗（无 review/generating_ticket）",
          "review" not in stages and "generating_ticket" not in stages, f"stages={stages}")
    check("S2 未输出草稿固定话术", "工单草稿已生成" not in reply, f"reply={reply[:80]}")
    check("S2 有正常回复文本", len(reply.strip()) >= 10, f"reply={reply[:80]}")

    print(f"\n═══ 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ═══")
    if FAIL:
        for f_ in FAIL:
            print("  失败:", f_)
        sys.exit(1)


asyncio.run(main())
