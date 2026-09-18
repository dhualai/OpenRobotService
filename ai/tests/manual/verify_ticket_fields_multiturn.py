"""提单字段收集多轮回归（真实入口 run_stream，重放 0824 生产对话）。

    python ai/tests/verify_ticket_fields_multiturn.py

生产事故：配置完了 发任务会报错 → 帮我转个工单 我有点急 → AI 只问了
1 个字段（报错详情）→ 用户答「没看清 一闪而过」→ 立刻生成草稿，描述里
把从没问过的字段（发生时间/设备型号/调度版本/现场联系人）全列成
「未提供/无」。

根因（两处，均已修）：
  1. fast-lane 规则 2 旧措辞「1-4 个缺口」→ 只挑 1 个字段，一答就齐。
     fast-lane 的 state_update.required_fields 先落地（pipeline.py:1011），
     _decide_ticket_fields 的修复版 prompt 根本不会跑 → 必须同步快路径措辞。
  2. _build_ticket 描述 prompt 的「（现场联系人、调度版本、发生时间、
     设备型号等）一项都不能丢」读起来像必列清单 → 没问过的字段被列成
     「未提供」。

修复后要求：
  T1 转工单诉求 → required_fields 2-4 个（≥2 核心），问一个问题，不弹草稿
  T2 「没看清 一闪而过」→ 继续问下一个核心字段，仍不弹草稿
  T3 补上第二个字段 → 弹草稿，描述不出现「未提供」占位，如实记录没看清
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


PRELOAD = [
    {"role": "user", "content": "配置完了 发任务会报错为啥呀"},
    {"role": "assistant",
     "content": "发任务报错可以先排查：1）检查网络连接是否正常；2）确认任务配置参数是否完整；"
                "3）看下调度服务日志里有没有报错记录。如果还报错，可以把报错内容发我看下。"},
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


async def load_state(agent, sid):
    memory = await agent._memory_manager.get_memory(sid)
    return memory.metadata.get("agent_state", {}), memory.metadata.get("ticket_draft") or {}


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        AiDiagnosisPlatform as AgentCls, AgentState, _save_agent_state)

    agent = AgentCls()
    await agent._ensure_clients()
    await agent._retriever.retrieve_domain_dual("预热", "team", top_k=1)

    sid = "verify_fields_mt"
    memory = await agent._memory_manager.get_memory(sid)
    memory.turns = [dict(t) for t in PRELOAD]
    st = AgentState(session_id=sid, phase="diagnosing",
                    original_query="配置完了 发任务会报错为啥呀")
    _save_agent_state(memory, st)
    await agent._memory_manager.save_memory(memory)

    # ── T1：转工单诉求 → 应问问题、字段 2-4 个、不弹草稿 ──
    print("\nT1 帮我转个工单 我有点急")
    stages, reply = await run_turn(agent, sid, "帮我转个工单 我有点急")
    print(f"    stages={stages}")
    print(f"    reply={reply[:120]}")
    s1, _ = await load_state(agent, sid)
    rf = s1.get("required_fields") or {}
    print(f"    required_fields={rf}")
    check("T1 未弹草稿", "review" not in stages and "generating_ticket" not in stages,
          f"stages={stages}")
    check("T1 有追问文本",
          any(k in reply for k in ("？", "?", "吗", "麻烦", "发我")), f"reply={reply[:80]}")
    check("T1 字段数 2-4", 2 <= len(rf) <= 4, f"rf={rf}")

    # ── T2：「没看清 一闪而过」→ 应继续问下一个核心字段，仍不弹草稿 ──
    print("\nT2 没看清 一闪而过")
    stages, reply = await run_turn(agent, sid, "没看清 一闪而过")
    print(f"    stages={stages}")
    print(f"    reply={reply[:120]}")
    check("T2 未弹草稿", "review" not in stages and "generating_ticket" not in stages,
          f"stages={stages}")
    check("T2 继续追问下一个字段", "？" in reply, f"reply={reply[:80]}")
    s2, _ = await load_state(agent, sid)
    missing_now = s2.get("ticket_collecting") or []
    print(f"    还在收集: {missing_now}")
    check("T2 仍有缺项在收集", len(missing_now) >= 1, f"ticket_collecting={missing_now}")

    # ── T3..Tn：按 AI 实际问的问题作答收尾（decide 的字段选择有抖动），
    # 每个回答都带上车辆编号，保证描述断言稳定 ──
    ANSWERS = [
        ("配置", "都是默认的没动过"),
        ("日志", "没有日志"),
        ("时间", "今天上午"),
        ("地图", "map_bbsw"),
        ("库位", "100256"),
        ("截图", "没有截图"),
        ("车", "就是3号车"),
        ("报错", "没看清，一闪而过"),
        ("型号", "不知道"),
    ]

    def answer_for(q: str) -> str:
        for kw, ans in ANSWERS:
            if kw in q:
                return ans
        return "不知道"

    drafted, reply3 = False, ""
    for i in range(3, 7):
        ans = answer_for(reply) + "，车是3号"
        print(f"\nT{i} 问:{reply[:60]!r} → 答: {ans}")
        stages, reply = await run_turn(agent, sid, ans)
        print(f"    stages={stages}")
        print(f"    reply={reply[:100]}")
        if "review" in stages or "generating_ticket" in stages or "工单草稿已生成" in reply:
            drafted, reply3 = True, reply
            break
        check(f"T{i} 未提前弹草稿",
              "review" not in stages and "generating_ticket" not in stages, f"stages={stages}")

    check("T3 弹出工单草稿", drafted, "6 轮内未生成草稿")
    s3, draft = await load_state(agent, sid)
    desc = str(draft.get("description") or "")
    print(f"    description={desc}")
    # 禁的是「没问过的字段列成未提供」凑格式；问过但用户答不知道的如实记录合法
    rf_labels = set((s3.get("required_fields") or {}).values())
    for f_ in ("发生时间", "现场联系人", "设备型号", "调度版本", "出现频率", "现场位置"):
        if f_ not in rf_labels:
            check(f"T3 未列从未问过的「{f_}」", f_ not in desc, desc[:120])
    check("T3 描述无「：无」占位", "：无" not in desc, desc[:150])
    honest = ("没看清" in desc or "一闪而过" in desc or "未看清" in desc)
    check("T3 如实记录报错没看清", honest, desc[:150])
    check("T3 记录了车辆编号", "3号" in desc, desc[:150])

    print(f"\n═══ 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ═══")
    if FAIL:
        for f_ in FAIL:
            print("  失败:", f_)
        sys.exit(1)


asyncio.run(main())
