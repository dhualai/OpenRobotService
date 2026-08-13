"""最终 E2E：真实 512MB 日志 logs_20260811_111608.zip 跑完整 orchestrator。

问题场景对齐真实日志：MAPF 规划慢 / 一致性超阈值 → 路径截断 → 重新规划。
验证 Discovery(错误优先) 能否让指挥 Agent 顺着真实错误「一致性校验/路径截断」走并给出结论。
"""
import sys, asyncio, time, json
from pathlib import Path

_ROOT = Path(r"D:\CodeHub\AI\OpenRobotService")
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / "ai" / ".env")

LOG = _ROOT / "ai" / "tests" / "test_logs" / "live" / "debug_logs.log.29"


async def main():
    if not LOG.exists():
        print(f"日志不存在: {LOG}")
        return

    import ai.agents.AiTaskPlatform.pipeline as p
    agent = p.AiTaskAgent()
    await agent._ensure_clients()

    from ai.agents.AiTaskPlatform.orchestrator import LogOrchestrator
    from ai.agents.AiTaskPlatform.log_analyzer.triage import run_triage
    from ai.agents.AiTaskPlatform.handlers.diagnose_flow import _discovery_to_text

    # 先打印 Discovery（复用 orchestrator 内部同一 index 需重建；此处单独 build 一次供预览）
    triage = run_triage(str(LOG), user_question="", manual_dir="")
    print("=" * 60)
    print("Discovery 文本（真实 512MB 日志）:")
    print(_discovery_to_text(triage))
    print("=" * 60)

    task_ctx = {
        "title": "AGV 运行异常，需要分析日志找根因",
        "problem_summary": "车辆运行异常，现场自报需要分析这次日志，找出根因",
    }
    discussion_history = "[工程师] 车出问题了，帮忙看下这批日志有什么异常"
    query = "帮我分析这份调度日志，看有什么错误或异常，根因是什么"

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
        d = r.get("directive") or {}
        dstr = json.dumps(d, ensure_ascii=False)
        hit = any(k in dstr for k in ("存在路径未被接收", "last_node", "避让", "一致性", "路径"))
        if hit:
            hit_follow = True
        mark = "▶命中自主发现的真实错误" if hit else "(未指向真实错误)"
        print(f"R{r['round']}: 命中{r.get('matched','?')}行 | {mark}")
        print(f"     directive: {dstr}")
    print("-" * 60)
    if hit_follow:
        print("✅ 指挥 Agent 顺着自主发现的真实错误走")
    else:
        print("⚠️ 未命中，需检查编排 prompt")
    if result["evidence"]:
        print("证据样例:")
        for e in result["evidence"][:6]:
            print(f"  L{e['line']}: {e['summary'][:100]}")


if __name__ == "__main__":
    asyncio.run(main())
