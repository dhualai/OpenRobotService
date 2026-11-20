"""diagnosis_nokb（诊断轮免检索）验证：真实 _classify_intent + 真实 run_stream 入口。

    python ai/tests/verify_intent_nokb.py

背景：诊断轮其实不都需要检索——续接轮（「然后呢」「还是不行」）和与设备无关的
通用对话（「你是谁」）靠上文即可作答。让意图模型先判出来，取消并发检索，
省下 rerank 等检索尾延。判断全交 LLM，代码只归一化意图 + 控路由。

S1 意图分类短语电池（真实 flash 模型）：
   - 排查上下文里的续接话术 → diagnosis_nokb
   - 同样上下文里抛**新问题** → 必须 diagnosis（安全方向：宁多查不漏查）
   - ticket / courtesy 不受影响
S2 全链路续接轮：run_stream 重放「然后呢」→ 日志出现 nokb 取消检索，
   回答承接上文进度（不说「未收录」）
S3 全链路真诊断：新故障问题 → 仍走「服务端检索」单轮分支（检索没被取消）
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


CTX = [
    {"role": "user", "content": "车辆调度任务下发失败怎么回事"},
    {"role": "assistant",
     "content": "任务下发失败可以按以下步骤排查：1）检查调度服务是否正常运行；"
                "2）确认车辆网络连接正常；3）在调度中心重新下发任务。"},
]

PRELOAD = [dict(t) for t in CTX]


class LogTap(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


async def run_turn(agent, sid, query, tap):
    from ai.agents.AiDiagnosisPlatform.pipeline import DiagnosisRequest
    root = logging.getLogger()
    root.addHandler(tap)
    try:
        stages, text = [], []
        async for ev in agent.run_stream(DiagnosisRequest(session_id=sid, query=query)):
            if ev.get("event") == "status":
                stages.append((ev["data"] or {}).get("stage", ""))
            elif ev.get("event") == "token":
                text.append(ev.get("data") or "")
        return stages, "".join(text)
    finally:
        root.removeHandler(tap)


async def s1_battery(agent):
    from ai.core import get_intent_client
    llm = await get_intent_client()

    print("\nS1 意图分类短语电池")
    # 续接/反馈话术（上文已给过资料）→ diagnosis_nokb
    for q in ("然后呢", "还是不行", "下一步呢"):
        r = await agent._classify_intent(llm, q, "", context_turns=CTX)
        print(f"    {q!r} → {r}")
        check(f"S1 续接「{q}」→ nokb", r == "diagnosis_nokb", f"got={r}")
    # 宽松组：确认/收尾话术，nokb 与 courtesy 都不触发检索，均可
    for q in ("好的我试试", "可以了"):
        r = await agent._classify_intent(llm, q, "", context_turns=CTX)
        print(f"    {q!r} → {r}")
        check(f"S1 确认「{q}」→ nokb/courtesy", r in ("diagnosis_nokb", "courtesy"),
              f"got={r}")
    # 安全方向：同样上下文里抛新问题 → 必须 diagnosis（要查知识库）
    for q in ("AGV 报错 2101 怎么处理", "工单流转流程是怎样的", "怎么重新下发地图配置"):
        r = await agent._classify_intent(llm, q, "", context_turns=CTX)
        print(f"    {q!r} → {r}")
        check(f"S1 新问题「{q[:12]}…」→ diagnosis", r == "diagnosis", f"got={r}")
    # ticket / courtesy 原样
    for q, want in (("帮我转个工单 我有点急", "ticket"), ("谢谢啦 辛苦了", "courtesy")):
        r = await agent._classify_intent(llm, q, "", context_turns=CTX)
        print(f"    {q!r} → {r}")
        check(f"S1 「{q[:8]}…」→ {want}", r == want, f"got={r}")
    # 通用对话（无设备上下文）：nokb 或 courtesy 均可
    r = await agent._classify_intent(llm, "你是谁啊", "", context_turns=[])
    print(f"    '你是谁啊' → {r}")
    check("S1 「你是谁」→ nokb/courtesy", r in ("diagnosis_nokb", "courtesy"), f"got={r}")


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        AiDiagnosisPlatform as AgentCls, AgentState, _save_agent_state)

    agent = AgentCls()
    await agent._ensure_clients()
    await agent._retriever.retrieve_domain_dual("预热", "team", top_k=1)

    await s1_battery(agent)

    # ── S2 全链路：排查上下文 +「然后呢」→ nokb 取消检索，承接上文 ──
    print("\nS2 全链路续接轮「然后呢」")
    sid = "verify_nokb_cont"
    memory = await agent._memory_manager.get_memory(sid)
    memory.turns = [dict(t) for t in PRELOAD]
    st = AgentState(session_id=sid, phase="diagnosing",
                    original_query="车辆调度任务下发失败怎么回事")
    _save_agent_state(memory, st)
    await agent._memory_manager.save_memory(memory)

    tap = LogTap()
    stages, reply = await run_turn(agent, sid, "然后呢", tap)
    joined = "\n".join(tap.lines)
    print(f"    stages={stages}")
    print(f"    reply={reply[:150]}")
    check("S2 日志出现 nokb 取消检索", "取消检索直接单轮" in joined, joined[-300:])
    check("S2 未等服务端检索", "诊断走单轮分支（服务端检索" not in joined,
          "仍走了检索路径")
    check("S2 回答承接上文（非未收录）", "未收录" not in reply and len(reply) > 15,
          reply[:120])

    # ── S3 全链路：全新故障问题 → 仍走服务端检索单轮分支 ──
    print("\nS3 全链路新诊断问题")
    sid3 = "verify_nokb_kb"
    tap3 = LogTap()
    stages, reply = await run_turn(agent, sid3, "AGV 报错 2101 怎么处理", tap3)
    joined3 = "\n".join(tap3.lines)
    print(f"    stages={stages}")
    print(f"    reply={reply[:120]}")
    check("S3 走服务端检索单轮分支", "诊断走单轮分支（服务端检索" in joined3, joined3[-300:])
    check("S3 未被 nokb 取消检索", "取消检索直接单轮" not in joined3, joined3[-300:])

    print(f"\n═══ 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ═══")
    if FAIL:
        for f_ in FAIL:
            print("  失败:", f_)
        sys.exit(1)


asyncio.run(main())
