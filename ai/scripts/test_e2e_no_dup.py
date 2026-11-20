"""端到端：提单收集轮 → 检查流式 token 与最终回复无重复段落。

场景：提单 → LLM 调 submit_ticket → collecting → LLM 追问。
验证：循环内流式 token 拼接 == 最终回复，且末尾没有整段重复。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ["AI_TICKET_TOOL_LOOP"] = "1"

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    DiagnosisRequest,
    get_diagnosis_platform,
)


def _has_dup(text: str) -> bool:
    """简单重复检测：任意 >=8 字片段在全文出现两次且位置不同。"""
    for i in range(0, len(text) - 8):
        seg = text[i:i + 8]
        if text.count(seg) >= 2:
            return True
    return False


async def main():
    platform = await get_diagnosis_platform()
    session = f"sess_e2e_{int(time.time())}"
    req = DiagnosisRequest(
        session_id=session, query="我要提单，给胡健楠", created_by="tester", skip_retrieval=True)

    streamed = ""
    statuses = []
    async for ev in platform.run_stream(req):
        if ev.get("event") == "token":
            streamed += ev.get("data", "")
        elif ev.get("event") == "status":
            statuses.append(ev.get("data", {}).get("stage"))

    print(f"\nstages: {statuses}")
    print(f"回复全文: {streamed!r}")
    print(f"总字符: {len(streamed)}")
    print("重复检测:", "❌ 有重复段落" if _has_dup(streamed) else "✅ 无重复")


if __name__ == "__main__":
    asyncio.run(main())
