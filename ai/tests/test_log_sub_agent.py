"""LogSubAgent 测试 — 真实调度日志 + 工单上下文"""
import sys, asyncio, time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "ai" / ".env")

LOG_FILE = Path(__file__).parent / "test_logs" / "debug_logs.log.1"


async def main():
    if not LOG_FILE.exists():
        print(f"日志文件不存在: {LOG_FILE}")
        return

    print(f"日志文件: {LOG_FILE} ({LOG_FILE.stat().st_size / 1024 / 1024:.0f} MB)")
    print(f"构建索引中...")

    from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent

    task_context = {
        "title": "任务I|1098000原子任务94234路径规划耗时过长",
        "description": "任务I|1098000下的原子任务94234规划路径卡了很久，大概2-3分钟才出路径。其他车上也看到类似的长时间规划",
        "problem_summary": "原子任务94234路径规划耗时2-3分钟，路径生成后才继续执行。怀疑路径规划算法耗时异常或ABORTED重试导致",
        "hypotheses": ["路径搜索空间过大", "ABORTED后重试延迟", "路径规划算法在大地图上耗时"],
        "ruled_out": ["网络延迟", "车辆硬件故障"],
        "robot_type": "潜伏车",
        "fault_code": "",
        "collected_info": {
            "任务ID": "I|1098000",
            "原子任务节点": "94234",
            "现象": "规划路径耗时2-3分钟",
            "时间窗口": "16:50~17:05",
            "路径状态": "ABORTED",
        },
    }

    t0 = time.perf_counter()
    agent = LogSubAgent(str(LOG_FILE))
    result = await agent.analyze(
        task_context=task_context,
        user_question="任务I|1098000的原子任务94234在16:55-16:58规划了很久。请先搜error_only=true找ABORTED、一致性校验失败、MAPF-T等异常关键词，找出根因后conclude。",
    )

    elapsed = time.perf_counter() - t0
    print(f"\n{'='*60}")
    print(f"LogSubAgent 分析完成 ({elapsed:.1f}s)")
    print(f"查询轮数: {result.queries_made}")
    print(f"证据条数: {len(result.evidence)}")
    print(f"兜底触发: {result.fallback_used}")
    print(f"{'='*60}")

    if result.conclusion:
        print(f"\n结论:\n{result.conclusion}")
    else:
        print("\n(无结论)")

    if result.evidence:
        print(f"\n关键证据行:")
        for e in result.evidence[:10]:
            print(f"  L{e['line']} | {e['summary'][:120]}")

    # 输出 Prompt 注入文本（供 diagnose/discuss 使用）
    print(f"\n{'='*60}")
    print("Prompt 注入文本:")
    print(result.to_prompt_text())


if __name__ == "__main__":
    asyncio.run(main())
