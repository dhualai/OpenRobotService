"""集成测试: 模拟 @小U "帮我分析日志" → discuss() → LogSubAgent 全链路"""
import sys, asyncio, time, zipfile, shutil, os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "ai" / ".env")

# 测试配置
ZIP_FILE = Path(__file__).parent / "test_logs" / "logs_20260728_135123.zip"
EXTRACT_DIR = Path(__file__).parent / "test_logs" / "zip_extracted"
LOG_IN_ZIP = "algo/DYNAMIC_MAP-USPA-LOGS-/debug_logs.log.16"


def extract_log_from_zip() -> Path | None:
    """从 ZIP 中提取日志文件，返回日志路径"""
    if not ZIP_FILE.exists():
        print(f"ZIP 不存在: {ZIP_FILE}")
        return None

    # 如果已解压，直接返回
    extracted = EXTRACT_DIR / LOG_IN_ZIP
    if extracted.exists():
        print(f"日志已解压: {extracted}")
        return extracted

    print(f"解压中: {ZIP_FILE}")
    with zipfile.ZipFile(ZIP_FILE) as zf:
        zf.extract(LOG_IN_ZIP, EXTRACT_DIR)
    print(f"解压完成: {extracted} ({extracted.stat().st_size / 1024 / 1024:.0f}MB)")
    return extracted


async def main():
    log_path = extract_log_from_zip()
    if not log_path:
        return

    # ── 模拟 discuss() 流程 ──
    from ai.agents.AiTaskPlatform.pipeline import AiTaskAgent

    agent = AiTaskAgent()
    await agent._ensure_clients()

    # 模拟工单上下文（和数据库里的工单内容一致）
    task_id = "test_1098000"
    ctx_title = "任务I|1098000原子任务94234路径规划耗时过长"
    ctx_desc = "任务I|1098000规划路径卡很久大概2-3分钟，其他车也有同样问题。重点看原子任务94234在16:55-16:58期间"

    # 模拟附件 → 触发 3b LogSubAgent
    attachments = [{"filename": log_path.name, "path": str(log_path.resolve())}]

    print(f"\n{'='*60}")
    print(f"模拟 @小U 讨论: task={task_id}")
    print(f"日志文件: {log_path.name} ({log_path.stat().st_size/1024/1024:.0f}MB)")
    print(f"问题: 帮我分析一下任务1098000的日志，94234这个原子任务规划为什么这么久")
    print(f"{'='*60}\n")

    # 构造和 discuss() 一样的 task_ctx
    task_ctx = {
        "title": ctx_title,
        "description": ctx_desc,
        "problem_summary": "原子任务94234路径规划耗时2-3分钟，怀疑一致性校验超阈值导致路径截断重复规划",
        "hypotheses": ["一致性超过update阈值导致路径截断", "MAPF规划耗时异常", "ABORTED后重试循环"],
        "ruled_out": ["网络延迟", "车辆硬件故障"],
        "robot_type": "XNA-169",
        "fault_code": "",
        "collected_info": {"任务ID": "I|1098000", "原子任务": "94234", "现象": "规划耗时2-3分钟"},
    }

    t0 = time.perf_counter()
    from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent

    sub = LogSubAgent(str(log_path))
    log_result = await sub.analyze(
        task_context=task_ctx,
        user_question="帮我分析一下任务1098000的日志，重点看16:55-17:00时间段的94234原子任务。为什么路径规划要2-3分钟？查一致性、MAPF-T、ABORTED、WARNING关键词。",
    )

    elapsed = time.perf_counter() - t0

    print(f"\n{'='*60}")
    print(f"LogSubAgent 完成 ({elapsed:.1f}s) | 轮数={log_result.queries_made} | 证据={len(log_result.evidence)} | 兜底={log_result.fallback_used}")
    print(f"{'='*60}")

    if log_result.conclusion:
        print(f"\n📋 结论:\n{log_result.conclusion}")
    else:
        print("\n⚠️ 无结论")

    if log_result.evidence:
        print(f"\n📊 关键证据 ({len(log_result.evidence)} 条):")
        for e in log_result.evidence[:10]:
            print(f"  L{e['line']}: {e['summary'][:150]}")

    # ── 最终一步：模拟 discuss() 的 LLM 回复 ──
    from ai.agents.AiTaskPlatform.prompts import DISCUSS_SYSTEM_PROMPT, DISCUSS_USER_TEMPLATE
    from ai.core import get_llm_client

    facultative = f"[日志子Agent分析（{log_result.queries_made}轮查询）]\n{log_result.to_prompt_text()}" if log_result.conclusion else ""
    llm = await get_llm_client()
    diag_summary = f"推测: {' / '.join(task_ctx.get('hypotheses', [])) if task_ctx.get('hypotheses') else '无'}"
    prompt = DISCUSS_USER_TEMPLATE.format(
        title=task_ctx["title"],
        description=task_ctx["description"][:200],
        diagnosis_summary=diag_summary,
        discussion_history="[工程师] 帮我分析一下任务1098000的日志，94234这个原子任务规划为什么这么久",
        query="帮我分析一下任务1098000的日志，94234这个原子任务规划为什么这么久",
        facultative_analysis=facultative,
    )
    reply = await llm.complete(
        prompt=prompt, system_prompt=DISCUSS_SYSTEM_PROMPT,
        max_tokens=600, temperature=0.4,
    )

    print(f"\n{'='*60}")
    print(f"🤖 小U 最终回复 (会写入 task_comments):")
    print(f"{'='*60}")
    print(reply.strip())


if __name__ == "__main__":
    asyncio.run(main())
