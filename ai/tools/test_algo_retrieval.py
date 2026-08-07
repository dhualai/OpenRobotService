"""Test retrieval quality with realistic diagnosis queries using the actual pipeline."""
import asyncio, os, sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

os.environ['HF_HUB_OFFLINE'] = '1'

from ai.core.retrieval import get_retrieval_service

async def main():
    svc = await get_retrieval_service()

    queries = [
        "任务分配迟迟不触发是什么原因",
        "多机器人路径规划出现死锁怎么办",
        "地图加载后路径规划返回空路径怎么排查",
        "Ray Actor启动失败怎么排查",
        "AI地图生成进度卡住不动了怎么办",
        "匈牙利算法在任务分配中怎么工作的",
        "路径规划超时TIMEOUT是什么原因导致的",
        "机器人频繁充电影响搬运效率怎么调整",
        "地图连通性验证报非强连通图警告",
        "交通锁死锁了怎么处理",
    ]

    print("=" * 80)
    print("Retrieval Quality Test — Algorithm Docs (usp/overview)")
    print("=" * 80)

    for query in queries:
        results = await svc.retrieve_domain(query, "team", top_k=3, sub_domain=r"usp\overview")
        if results:
            top = results[0]
            # show score and which doc it matched
            print(f"\nQ: {query}")
            print(f"   score={top.score:.4f}  title: {top.title[:95]}")
            if len(results) > 1:
                print(f"   score={results[1].score:.4f}  title: {results[1].title[:95]}")
        else:
            print(f"\nQ: {query}")
            print(f"   *** NO RESULTS ***")

if __name__ == '__main__':
    asyncio.run(main())
