"""诊断：vehicle_motion「运动控制日志在哪看」点为何检索不到
用法：在项目根目录 `python diag_vehicle_motion.py`（生产/本地均可，自适应 server/local qdrant）
"""
import asyncio
import math
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv("ai/.env")


async def main():
    from qdrant_client import QdrantClient, models
    from ai.core.embed import get_embed_client
    from ai.config import get_active_collection_for

    col = get_active_collection_for("company")
    print("1) company 指针集合:", col or "(空)")
    print("   .env EMBEDDING_MODEL_NAME:",
          os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-base-zh-v1.5"))

    lp = os.getenv("QDRANT_LOCAL_PATH", "").strip()
    if lp:
        qc = QdrantClient(path=lp)
        print("   qdrant 模式: local", lp)
    else:
        qc = QdrantClient(
            host=os.getenv("QDRANT_HOST", "localhost"),
            port=int(os.getenv("QDRANT_PORT", "6333")),
            check_compatibility=False,
        )
        print("   qdrant 模式: server")

    vm_filter = models.Filter(must=[models.FieldCondition(
        key="sub_domain", match=models.MatchValue(value="vehicle_motion"))])

    try:
        info = qc.get_collection(col)
        try:
            sizes = {k: getattr(v, "size", v)
                     for k, v in info.config.params.vectors.items()}
        except Exception:
            sizes = getattr(info.config.params.vectors, "size", "?")
        print(f"   集合点数={info.points_count} 向量维度={sizes}")
    except Exception as e:
        print("   集合信息读取失败:", e)

    def find_target(points):
        return [p for p in points
                if "日志在哪看" in (p.payload or {}).get("title", "")]

    r, _ = qc.scroll(collection_name=col, scroll_filter=vm_filter,
                     limit=100, with_payload=True, with_vectors=True)
    targets = find_target(r)
    print(f"2) 指针集合 vehicle_motion 点数: {len(r)}，含「日志在哪看」: {len(targets)}")

    if not targets:
        print("   !! 指针集合没有目标点 —— 扫全部 company 集合:")
        cols = [c.name for c in qc.get_collections().collections
                if c.name.startswith("company")]
        for cn in cols:
            try:
                info = qc.get_collection(cn)
                r2, _ = qc.scroll(collection_name=cn, scroll_filter=vm_filter,
                                  limit=100, with_payload={"include": ["title"]})
                has = bool(find_target(r2))
                print(f"   - {cn}: points={info.points_count} "
                      f"vehicle_motion={len(r2)} 含日志在哪看={has}")
            except Exception as e:
                print(f"   - {cn}: 查询失败 {e}")
        return

    p = targets[0]
    vec = p.vector or {}
    dense = vec.get("dense") if isinstance(vec, dict) else vec
    print(f"   目标点: {p.payload.get('title', '')[:60]}")
    if dense:
        norm = math.sqrt(sum(x * x for x in dense))
        zero = "  !! 全零向量" if norm < 1e-6 else ""
        print(f"   dense 维度={len(dense)} norm={norm:.4f}{zero}")
    else:
        print("   !! 目标点没有 dense 向量")

    ec = await get_embed_client()
    qv = await ec.embed("车端日志查看获取方法")
    res = qc.query_points(collection_name=col, query=qv.tolist(),
                          using="dense", limit=10, with_payload=True)
    print("3) dense 搜索「车端日志查看获取方法」top10:")
    target_ids = {t.id for t in targets}
    hit = False
    for i, pt in enumerate(res.points, 1):
        t = (pt.payload or {}).get("title", "")
        mark = "  ←目标" if pt.id in target_ids else ""
        if mark:
            hit = True
        print(f"   {i}. [{pt.score:.4f}] ({pt.payload.get('sub_domain')}) {t[:46]}{mark}")
    if not hit:
        print("   !! 目标点未进 dense top10 —— 向量空间错位（换过 embedding 模型）或该点向量损坏")

    qc.close()


asyncio.run(main())
