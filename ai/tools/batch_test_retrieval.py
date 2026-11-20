"""批量检索验证 — 用全新问题测试，排除"对症下药"嫌疑

Usage:
    python ai/tools/batch_test_retrieval.py
"""
import asyncio, io, os, sys, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, 'ai', '.env'))

from ai.core.retrieval import get_retrieval_service

# ── 全新测试题（不在原 14 题中）──────────────────────────────────

NEW_QUERIES = [
    # 原测试集之外的现场问题
    ("急停恢复",     "机器人急停拔掉后还是不动，无法恢复自动"),
    ("地图同步",     "地图编辑器改了路网，但车还是走老路"),
    ("无故停止",     "车走着走着突然停了，前面没有任何障碍物"),
    ("充电调度",     "明明有空闲充电桩，但车就是不去充"),
    ("库位参数",     "库位的操作高度在页面上改了但实际不生效"),
    ("路径规划",     "机器人一直在原地绕圈，找不到去目标点的路"),
    ("MQTT断连",    "mqtt断开后所有车都失联了，怎么恢复"),
    ("预处理卡死",   "地图预处理点了之后一直转圈，几个小时不结束"),
    ("任务取消",     "任务已经在页面取消了，但车还在继续执行"),
    ("叉车取货",     "叉车取货时货叉插不进去，对不准托盘"),
]


async def test_one(service, query: str, label: str) -> dict:
    """单次检索，返回 top-3 的标题和分数"""
    results, _ = await service.retrieve(query, top_k=3, check_confidence=False)
    top3 = []
    for r in results[:3]:
        top3.append({
            "title": r.title or "(无标题)",
            "sub_domain": r.sub_domain or "-",
            "score": round(r.score, 4),
            "content_head": r.content[:80].replace('\n', ' ').strip(),
        })
    return {"label": label, "query": query, "top3": top3, "total_hits": len(results)}


async def main():
    service = await get_retrieval_service()
    t0 = time.perf_counter()

    print(f"\n{'='*80}")
    print(f"  全新问题批量检索验证（10 题，均不在原训练/测试集中）")
    print(f"{'='*80}")

    tasks = [test_one(service, q, label) for label, q in NEW_QUERIES]
    results = await asyncio.gather(*tasks)

    # ── 汇总 ──
    print(f"\n{'─'*80}")
    print(f"  📊 汇总结果")
    print(f"{'─'*80}\n")

    for r in results:
        print(f"  [{r['label']}] {r['query']}")
        if not r['top3']:
            print(f"     ⚠️ 无结果")
        for i, hit in enumerate(r['top3']):
            flag = " 👈" if i == 0 else "  "
            print(f"     #{i+1}{flag} [{hit['score']}] [{hit['sub_domain']}] {hit['title']}")
            print(f"         {hit['content_head']}")
        print()

    total_ms = (time.perf_counter() - t0) * 1000
    print(f"  总耗时: {total_ms:.0f}ms\n")


if __name__ == '__main__':
    asyncio.run(main())
