"""诊断：直接测试 retrieve_cheduan"""
import sys, asyncio
from pathlib import Path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

async def main():
    from ai.core.retrieval import get_retrieval_service

    retriever = await get_retrieval_service()

    # 测试 _extract_error_codes
    from ai.core.retrieval import RetrievalService
    codes = RetrievalService._extract_error_codes("错误码6301什么情况")
    print(f"提取到的错误码: {codes}")

    # 测试 retrieve_cheduan
    results = await retriever.retrieve_cheduan("错误码6301什么情况", top_k=3)
    print(f"retrieve_cheduan 返回数量: {len(results)}")
    for r in results:
        print(f"  title={r.title}")
        print(f"  content={r.content[:200]}...")
        print(f"  score={r.score}")
        print()

    if not results:
        # 手动检查
        from ai.config import get_active_cheduan_collection
        col = get_active_cheduan_collection()
        print(f"活跃集合: {col}")
        print(f"Qdrant 可用: {not retriever._qdrant.is_unavailable}")
        print()

        # 直接查 Qdrant
        from ai.config import get_ai_config
        cfg = get_ai_config()
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        c = QdrantClient(host=cfg.qdrant_host, port=cfg.qdrant_port, check_compatibility=False)
        hits, _ = c.scroll(
            collection_name=col,
            scroll_filter=Filter(must=[FieldCondition(key="error_code", match=MatchAny(any=["6301"]))]),
            limit=3,
        )
        print(f"直接查询 6301: {len(hits)} 命中")
        if hits:
            p = hits[0].payload
            print(f"  error_code={p.get('error_code')}, desc={p.get('description_cn','')[:80]}")

asyncio.run(main())
