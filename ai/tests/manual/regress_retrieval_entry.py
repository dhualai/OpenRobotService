"""检索链路回归（真实入口：构造 AgentState → _retrieve_with_context）。

    python ai/tests/regress_retrieval_entry.py

覆盖场景：
  S1 新会话完整问题（AI绘制地图）
  S2 diagnosing 会话换新话题（0824 事故场景：残留 original_query 不得顶掉新问题）
  S3 diagnosing 会话省略式追问（rewrite 按上下文补全）
  S4 诊断工具循环 query_override（LLM 自组查询词）
  S5 车端错误码精确命中
  S6 检索缓存按 session 隔离（跨会话不串味）
"""
import asyncio, io, os, sys, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))


def titles(docs: str):
    return re.findall(r"（([^）]{0,60})）：", docs)


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform as AgentCls, AgentState

    agent = AgentCls()
    await agent._ensure_clients()
    await agent._retriever.retrieve_domain_dual("预热", "team", top_k=1)

    def state(phase="diagnosing", original_query="", problem_summary="", **kw):
        return AgentState(session_id="regress", phase=phase,
                          original_query=original_query,
                          problem_summary=problem_summary, **kw)

    # ── S1 新会话完整问题 ──
    print("\nS1 新会话完整问题：如何使用AI绘制地图？")
    st = state(phase="idle", original_query="如何使用AI绘制地图？",
               problem_summary="如何使用AI绘制地图？")
    docs = await agent._retrieve_with_context("s1", st, query_override="如何使用AI绘制地图？")
    check("S1 召回 AI 地图内容", ("AI 地图生成" in docs) or ("5.15" in docs))

    # ── S2 事故场景：diagnosing 残留旧问题 + 本轮换新话题 ──
    print("\nS2 diagnosing 会话换新话题（残留 original_query=库位操作高度）")
    st = state(original_query="库位改操作高度不生效怎么办？",
               problem_summary="库位改操作高度不生效")
    docs = await agent._retrieve_with_context(
        "s2", st,
        context_turns=[{"role": "user", "content": "库位改操作高度不生效怎么办？"},
                       {"role": "assistant", "content": "打开库位管理检查操作高度配置。"}],
        query_override="如何使用AI绘制地图？")
    check("S2 检索词用本轮问题（召回 AI 地图）", ("AI 地图生成" in docs) or ("5.15" in docs))

    # ── S3 省略式追问：rewrite 按上下文补全 ──
    print("\nS3 diagnosing 省略式追问：没急停，就是突然不工作了")
    st = state(original_query="车不动了 任务执行中怎么办？", problem_summary="机器人不动")
    docs = await agent._retrieve_with_context(
        "s3", st,
        context_turns=[{"role": "user", "content": "车不动了 任务执行中怎么办"},
                       {"role": "assistant", "content": "先确认机器人是否处于急停状态。"}],
        query_override="没急停，就是突然不工作了")
    _t = " ".join(titles(docs)) + docs[:200]
    check("S3 召回机器人不动/任务相关", ("机器人" in _t or "任务" in _t) and "库位" not in _t[:120])

    # ── S4 诊断工具循环：LLM 自组查询词 ──
    print("\nS4 工具循环 query_override：电梯调度 任务模式")
    st = state(original_query="电梯怎么用", problem_summary="电梯调度")
    docs = await agent._retrieve_with_context(
        "s4", st, context_turns=[{"role": "user", "content": "电梯和车怎么配合"}],
        query_override="电梯多层调度 任务模式 车随梯")
    check("S4 召回电梯模式内容", "电梯" in docs)

    # ── S5 车端错误码精确命中 ──
    print("\nS5 车端错误码：错误码6301什么情况")
    st = state(phase="idle", original_query="错误码6301什么情况",
               problem_summary="错误码6301什么情况")
    docs = await agent._retrieve_with_context("s5", st, query_override="错误码6301什么情况")
    check("S5 精确命中 6301", "6301" in docs)

    # ── S6 缓存按 session 隔离 ──
    print("\nS6 检索缓存 session 隔离（同查询两会话 → 两个缓存条目）")
    st = state(original_query="如何使用AI绘制地图？", problem_summary="AI绘制地图")
    await agent._retrieve_with_context("sessA", st, query_override="如何使用AI绘制地图？")
    st_b = state(original_query="如何使用AI绘制地图？", problem_summary="AI绘制地图")
    await agent._retrieve_with_context("sessB", st_b, query_override="如何使用AI绘制地图？")
    keys = [k for k in agent._retrieval_cache
            if k.endswith("如何使用AI绘制地图？")]
    check("S6 缓存 key 含 session 前缀（sessA/sessB 各一条）",
          any(k.startswith("sessA:") for k in keys) and any(k.startswith("sessB:") for k in keys),
          f"keys={keys}")

    print(f"\n═══ 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ═══")
    if FAIL:
        for f_ in FAIL:
            print("  失败:", f_)
        sys.exit(1)


asyncio.run(main())
