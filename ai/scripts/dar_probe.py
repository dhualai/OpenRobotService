# -*- coding: utf-8 -*-
"""检索探针：单问题检索重放，看知识库命中（可连测试/生产远程只读）。

用法：
    python ai/scripts/dar_probe.py --q "AGV 怎么上线部署"                 # 缺省连测试环境知识库
    python ai/scripts/dar_probe.py --q "..." --qdrant prod              # 生产（只读）
    python ai/scripts/dar_probe.py --q "..." --qdrant local             # 本地
输出 JSON（stdout）：{query, qdrant, pointers, ctx_len, n_chunks, chunks}
chunks 复用 dar_retrieval_check.parse_retrieval_chunks（route/title/text）。
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dar_retrieval_check import parse_retrieval_chunks  # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(q: str, qdrant: str) -> dict:
    if qdrant in ("prod", "test"):
        from dar_qdrant import remote_qdrant
        with remote_qdrant(qdrant) as ptr:
            return await _do(q, qdrant, ptr)
    return await _do(q, qdrant, {})


async def _do(q: str, qdrant: str, ptr: dict) -> dict:
    os.chdir(PROJ)
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState, get_diagnosis_platform
    platform = await get_diagnosis_platform()
    await platform._ensure_clients()
    # 探针是冷进程（subprocess.run）：首次检索常因 reranker 加载 + 大集合检索串行超时
    # 吞域。monkey patch _three_way_retrieve 里的 wait_for 把超时放长到 120s，
    # 让热启动能完成；第二次调用拿热态结果才是探针要看的内容。
    import asyncio as _asyncio
    state = AgentState(session_id=f"dar_probe_{os.getpid()}", original_query=q)
    if hasattr(platform, "_three_way_retrieve"):
        _orig_one = platform._three_way_retrieve.__wrapped__ if hasattr(platform._three_way_retrieve, "__wrapped__") else None
        if _orig_one is None:
            # 直接 replace 整个方法
            _orig_three = platform._three_way_retrieve
            async def _three_long(query):
                async def _one(domain, top_k):
                    try:
                        dense_res, sparse_res = await _asyncio.wait_for(
                            platform._retriever.retrieve_domain_dual(query, domain, top_k=8),
                            timeout=120.0,
                        )
                        for r in list(dense_res) + list(sparse_res):
                            r.domain = domain
                        return list(dense_res)[:top_k], list(sparse_res)[:top_k]
                    except Exception as e:
                        return [], []
                t = _asyncio.create_task(_one("team", 5))
                c = _asyncio.create_task(_one("company", 4))
                i = _asyncio.create_task(_one("industry", 3))
                gathered = await _asyncio.gather(t, c, i, return_exceptions=True)
                # 还原原始 _three_way_retrieve 的后续处理结构（list of
                # RetrievalResult），下游按 .id 访问属性。
                results = []
                seen = set()
                for grp in gathered:
                    if isinstance(grp, BaseException):
                        continue
                    for sub in grp:        # grp = (dense[:top_k], sparse[:top_k])
                        for r in sub:
                            if id(r) not in seen and getattr(r, "id", None) is not None:
                                seen.add(id(r))
                                results.append(r)
                return results
            platform._three_way_retrieve = _three_long
            try:
                ctx = await platform._retrieve_with_context(state.session_id, state)
                ctx_warm = await platform._retrieve_with_context(state.session_id, state)
            finally:
                platform._three_way_retrieve = _orig_three
        else:
            ctx_warm = await platform._retrieve_with_context(state.session_id, state)
    else:
        ctx = await platform._retrieve_with_context(state.session_id, state)
        ctx_warm = ctx
    chunks = parse_retrieval_chunks(ctx_warm, max_chunks=20, text_cap=300)
    return {"query": q, "qdrant": qdrant, "pointers": ptr,
            "ctx_len": len(ctx_warm or ""), "n_chunks": (ctx_warm or "").count("---") // 2,
            "chunks": chunks}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True)
    ap.add_argument("--qdrant", default="test", choices=["test", "prod", "local"])
    a = ap.parse_args()
    try:
        result = asyncio.run(run(a.q, a.qdrant))
        result["ok"] = True
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=1))
