"""验证工具循环 token 是否随到随发（流式），而非一轮结束后一坨。

用会产出长回复的场景（咨询类 → LLM 查完知识库后长回答）。
流式 = token 事件之间有明显时间间隔（逐字渲染）；
非流式 = 全部 token 事件在几毫秒内瞬间到达（整段累积后 burst）。

运行：python ai/scripts/test_stream_timing.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ["AI_DIAGNOSIS_TOOL_LOOP"] = "1"

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    DiagnosisRequest,
    get_diagnosis_platform,
)


async def main():
    platform = await get_diagnosis_platform()
    session = f"sess_stream_timing_{int(time.time())}"
    req = DiagnosisRequest(
        session_id=session,
        query="AGV 上不了轨锁区一般是什么原因？排查步骤给我说详细一点",
        created_by="tester",
    )

    t_start = time.perf_counter()
    times = []  # (相对起点, 事件)
    async for ev in platform.run_stream(req):
        if ev.get("event") == "token":
            times.append((time.perf_counter() - t_start, ev.get("data", "")))

    n = len(times)
    total_chars = sum(len(t) for _, t in times)
    print(f"\ntoken 事件数: {n}, 正文总字符: {total_chars}")
    if not times:
        print("❌ 没有任何 token 事件")
        return
    span = times[-1][0] - times[0][0]
    # token 之间的间隔分布
    gaps = [times[i][0] - times[i - 1][0] for i in range(1, n)]
    big_gaps = [g for g in gaps if g > 0.05]  # >50ms 的间隔
    print(f"首 token 到达: {times[0][0]:.2f}s 后（含 thinking 延迟）")
    print(f"首 token → 末 token 间隔: {span:.2f}s")
    print(f"token 间隔 >50ms 的次数: {len(big_gaps)}/{len(gaps)}")
    print(f"最长间隔: {max(gaps) if gaps else 0:.2f}s")
    if n >= 20 and len(big_gaps) >= 3:
        print("✅ 流式正常（token 随时间陆续到达，非一次性吐出）")
    else:
        print("❌ 疑似非流式（token 一次性 burst）")


if __name__ == "__main__":
    asyncio.run(main())
