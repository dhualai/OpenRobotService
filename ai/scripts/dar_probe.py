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
    state = AgentState(session_id=f"dar_probe_{os.getpid()}", original_query=q)
    ctx = await platform._retrieve_with_context(state.session_id, state)
    chunks = parse_retrieval_chunks(ctx, max_chunks=20, text_cap=300)
    return {"query": q, "qdrant": qdrant, "pointers": ptr,
            "ctx_len": len(ctx or ""), "n_chunks": (ctx or "").count("---") // 2,
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
