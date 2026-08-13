"""端到端验证 orchestrator 循环编排（真实日志 + 真实 LLM）。

用例（对齐 8084 通信问题的排障场景）：用户问日志，orchestrator 走
Discovery → 编排 LLM 规划 → LogSubAgent 执行 directive → 回归 → 结论。
"""
import sys, asyncio, time, json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / "ai" / ".env")


async def main():
    LOG = Path(__file__).parent / "test_logs" / "debug_logs.log.1"
    if not LOG.exists():
        print(f"日志不存在: {LOG}")
        return

    import ai.agents.AiTaskPlatform.pipeline as p
    agent = p.AiTaskAgent()
    await agent._ensure_clients()

    from ai.agents.AiTaskPlatform.orchestrator import LogOrchestrator
    from ai.agents.AiTaskPlatform.log_analyzer.triage import run_triage
    from ai.agents.AiTaskPlatform.handlers.diagnose_flow import _discovery_to_text

    # 先打印 Discovery（应把 last_node_index校验失败 排在前面）
    triage = run_triage(str(LOG), user_question="", manual_dir="")
    print("=" * 60)
    print("Discovery 文本（应优先呈现真实 ERROR）:")
    print(_discovery_to_text(triage))
    print("=" * 60)

    task_ctx = {
        "title": "AGV 路径规划失败 last_node_index校验失败",
        "problem_summary": "日志里大量 last_node_index校验失败: 当前位置与last_node_index不匹配，车 XNA-124 等频繁报错",
        "robot_type": "XNA",
    }
    discussion_history = "[工程师] 各车一直报 last_node_index 校验失败，位置对不上，怀疑定位/里程计问题"
    query = "日志里大量 last_node_index校验失败，帮我看 root cause 是什么"

    orch = LogOrchestrator(agent, str(LOG))
    t0 = time.perf_counter()
    result = await orch.run(task_ctx=task_ctx, discussion_history=discussion_history, query=query)
    elapsed = time.perf_counter() - t0

    print("=" * 60)
    print(f"Orchestrator 完成 ({elapsed:.1f}s)")
    print(f"编排轮数: {len(result['rounds'])}")
    print(f"证据条数: {len(result['evidence'])}")
    print(f"兜底: {result['fallback']}")
    print("-" * 60)
    if result["conclusion"]:
        print(f"结论:\n{result['conclusion']}")
    print("-" * 60)
    hit_follow = False
    for r in result["rounds"]:
        d = r.get('directive') or {}
        dstr = json.dumps(d, ensure_ascii=False)
        hit_ln = "last_node_index" in dstr or "last_node" in dstr
        if hit_ln:
            hit_follow = True
        mark = "▶命中last_node_index" if hit_ln else "(未直接指向last_node_index)"
        print(f"R{r['round']}: 命中{r.get('matched','?')}行 | {mark}")
        print(f"     directive: {dstr}")
    print("-" * 60)
    if hit_follow:
        print("✅ investigate 编排顺着真实错误 last_node_index 走")
    else:
        print("⚠️ investigate 未命中 last_node_index，需要检查编排 prompt")
    if result["evidence"]:
        print("证据样例:")
        for e in result["evidence"][:5]:
            print(f"  L{e['line']}: {e['summary'][:100]}")


if __name__ == "__main__":
    asyncio.run(main())
