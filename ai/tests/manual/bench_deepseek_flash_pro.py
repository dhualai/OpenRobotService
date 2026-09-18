"""deepseek flash vs pro 开 low 思考的延迟对比。

    python ai/tests/bench_deepseek_flash_pro.py

同一诊断类 prompt，两模型交替各跑 3 轮（thinking=enabled, effort=low），
附 1 轮关思考基线做参照。
"""
import asyncio, io, os, sys, time, statistics

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))

from ai.core.llm import LLMClient

PROMPT = (
    "现场机器人 XCD151 报错 2101，充电桩上充不进电，从今天早上开始一直如此。"
    "请简要给出排查步骤（3-4 条即可）。"
)

RUNS = 3


async def timed(client: LLMClient, label: str):
    t0 = time.perf_counter()
    out = await client.complete(prompt=PROMPT, max_tokens=500, temperature=0.3,
                                thinking=True)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  {label:24s} {ms:8.0f} ms   输出 {len(out)} 字")
    return ms


async def main():
    flash = LLMClient(model="deepseek-v4-flash", reasoning_effort="low")
    pro = LLMClient(model="deepseek-v4-pro", reasoning_effort="low")

    print("连通性探测：")
    for name, c in (("flash", flash), ("pro", pro)):
        try:
            await timed(c, f"{name} probe")
        except Exception as e:
            print(f"  {name} 不可用: {str(e)[:200]}")
            return

    f_ms, p_ms = [], []
    print(f"\n正式对比（thinking=low，各 {RUNS} 轮交替）：")
    for i in range(RUNS):
        f_ms.append(await timed(flash, f"flash #{i+1}"))
        p_ms.append(await timed(pro, f"pro   #{i+1}"))

    print("\n关思考基线（各 1 轮）：")
    flash_off = LLMClient(model="deepseek-v4-flash", reasoning_effort="off")
    pro_off = LLMClient(model="deepseek-v4-pro", reasoning_effort="off")
    f_off = await timed(flash_off, "flash 关思考")
    p_off = await timed(pro_off, "pro   关思考")

    print("\n═══ 汇总 ═══")
    print(f"flash low思考: 均值 {statistics.mean(f_ms):7.0f} ms  中位 {statistics.median(f_ms):7.0f} ms")
    print(f"pro   low思考: 均值 {statistics.mean(p_ms):7.0f} ms  中位 {statistics.median(p_ms):7.0f} ms")
    print(f"pro/flash 倍率: {statistics.mean(p_ms)/statistics.mean(f_ms):.1f}x")
    print(f"关思考基线:  flash {f_off:.0f} ms / pro {p_off:.0f} ms")


asyncio.run(main())
