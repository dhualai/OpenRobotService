"""「信息收集超限」误触发回归（真实入口 run_stream，重放 0824 生产对话）。

    python ai/tests/verify_collect_rounds_reset.py

生产事故：4 个字段问齐 → 「没有截图」生成草稿（collect_rounds 计到 4 未
复位）→ 用户补充「项目是摇人吧项目」→ collect_rounds +1 → 被判超限强制
提单，回复「工单草稿已生成（信息收集超限）」，用户完全看不懂。

修复后要求：
  T1-Tn 收集轮正常一问一答，字段答齐 → 正常生成草稿（无「超限」字样）
  Tn+1 草稿后补充项目 → 重建草稿重发弹窗 + 项目预填播报，
  绝不出现「信息收集超限」，收集计数归零
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


# 按 AI 上一轮实际问的问题作答（模拟真实用户：问什么答什么）
ANSWERS = [
    ("地图", "map_bbsw"),
    ("库位", "100256"),
    ("截图", "没有截图"),
    ("图片", "没有截图"),
    ("照片", "没有截图"),
    ("设备", "库位管理页面"),
    ("模块", "库位管理页面"),
    ("页面", "库位管理页面"),
    ("功能", "库位管理页面"),
    ("车", "3号车"),
    ("编号", "100256"),
    ("型号", "不知道"),
    ("报错", "保存的时候报错"),
    ("现象", "保存的时候报错"),
    ("描述", "保存的时候报错"),
    ("什么问题", "保存的时候报错"),
    ("时间", "今天上午"),
    ("版本", "不知道"),
    ("联系", "没有"),
]


def answer_for(question: str) -> str:
    for kw, ans in ANSWERS:
        if kw in str(question):
            return ans
    return "不知道"


async def run_turn(agent, sid, query):
    from ai.agents.AiDiagnosisPlatform.pipeline import DiagnosisRequest
    stages, text = [], []
    async for ev in agent.run_stream(DiagnosisRequest(session_id=sid, query=query)):
        if ev.get("event") == "status":
            stages.append((ev["data"] or {}).get("stage", ""))
        elif ev.get("event") == "token":
            text.append(ev.get("data") or "")
    return stages, "".join(text)


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform as AgentCls

    agent = AgentCls()
    await agent._ensure_clients()
    await agent._retriever.retrieve_domain_dual("预热", "team", top_k=1)

    # 本地无生产库：注入用户项目列表，走通 project_choice 预填管道
    async def fake_projects(_uid):
        return [{"name": "摇人吧服务号", "code": "YRB-001"}]
    agent._get_user_projects = fake_projects

    sid = "verify_rounds_reset"

    # ── T1：提单诉求 ──
    print("\nT1 有点弄不好 帮我提个单吧")
    stages, reply = await run_turn(agent, sid, "有点弄不好 帮我提个单吧")
    print(f"    stages={stages}")
    print(f"    reply={reply[:100]}")
    check("T1 未弹草稿", "review" not in stages and "generating_ticket" not in stages,
          f"stages={stages}")

    # ── T2..Tn：按 AI 实际问的问题一问一答，直到草稿生成 ──
    drafted = False
    draft_reply = ""
    for i in range(2, 8):
        ans = answer_for(reply)
        print(f"\nT{i} 问:{reply[:50]!r} → 答: {ans}")
        stages, reply = await run_turn(agent, sid, ans)
        print(f"    stages={stages}")
        print(f"    reply={reply[:100]}")
        if "review" in stages or "generating_ticket" in stages:
            drafted = True
            draft_reply = reply
            break
        check(f"T{i} 收集轮未提前弹草稿",
              "review" not in stages and "generating_ticket" not in stages, f"stages={stages}")

    check("收集完成生成草稿", drafted, "6 轮内未生成草稿")
    check("首次草稿无「信息收集超限」", "信息收集超限" not in draft_reply,
          draft_reply[:100])

    # ── Tn+1：草稿后补充项目 → 重建草稿 + 预填播报，绝不出现「超限」 ──
    print("\nT+1 项目是摇人吧项目")
    stages, reply = await run_turn(agent, sid, "项目是摇人吧项目")
    print(f"    stages={stages}")
    print(f"    reply={reply[:150]}")
    check("补充项目轮无「信息收集超限」", "信息收集超限" not in reply, reply[:150])
    check("项目预填已播报", "摇人吧服务号" in reply or "预填" in reply, reply[:150])
    memory = await agent._memory_manager.get_memory(sid)
    s = memory.metadata.get("agent_state", {})
    print(f"    collect_rounds={s.get('collect_rounds')} "
          f"ticket_collecting={s.get('ticket_collecting')}")
    check("收集计数已归零", int(s.get("collect_rounds", -1) or 0) == 0,
          f"collect_rounds={s.get('collect_rounds')}")

    print(f"\n═══ 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ═══")
    if FAIL:
        for f_ in FAIL:
            print("  失败:", f_)
        sys.exit(1)


asyncio.run(main())
