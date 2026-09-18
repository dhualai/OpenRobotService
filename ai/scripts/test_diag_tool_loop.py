"""诊断工具循环验证（真实 LLM + 真实检索）。

验证场景：
1. 咨询类问题 → LLM 自己调 search_kb → 基于检索结果回答
2. 咨询不满意 → 提单 → submit_ticket 工具 → 追问/弹窗

运行：python ai/scripts/test_diag_tool_loop.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ["AI_DIAGNOSIS_TOOL_LOOP"] = "1"

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    DiagnosisRequest,
    get_diagnosis_platform,
)


async def run_round(platform, session, query, label):
    req = DiagnosisRequest(
        session_id=session, query=query, created_by="tester")  # 不 skip_retrieval：意图分类才会跑
    stages, tokens, review_draft = [], [], None
    async for ev in platform.run_stream(req):
        if ev.get("event") == "status":
            stages.append(ev.get("data", {}).get("stage"))
            if ev.get("data", {}).get("draft"):
                review_draft = ev["data"]["draft"]
        elif ev.get("event") == "token":
            tokens.append(ev["data"])
    print(f"\n=== {label} ===")
    print("stages:", stages)
    print("回复:", "".join(tokens)[:300])
    return review_draft


async def main():
    platform = await get_diagnosis_platform()
    session = "sess_diag_tool_001"

    # 场景 1：咨询 → LLM 自己查知识库 → 回答
    await run_round(platform, session, "车没有上轨锁区怎么处理？", "咨询类（应查知识库）")

    # 场景 2：提单 → LLM 调 submit_ticket
    d = await run_round(platform, session, "我搞不懂，帮我提单给胡健楠吧", "提单（咨询转提单）")
    if d:
        print("✅ 草稿:", d.get("title"), "|", str(d.get("description"))[:100])

    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
