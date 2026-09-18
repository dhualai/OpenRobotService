import asyncio
import json
import os
import sys
from pathlib import Path

# 当前工作目录通常是 ai/，确保仓库根目录可导入 ai 包
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT / "ai")

from dotenv import load_dotenv
load_dotenv(".env", override=False)
os.environ["LLM_BACKEND"] = "relay"

from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform, DiagnosisRequest
from ai.agents.AiDiagnosisPlatform.pipeline import AgentState


async def main():
    platform = AiDiagnosisPlatform()
    await platform._ensure_clients()
    print(f"模型: {platform._llm_client.model}")
    print(f"地址: {platform._llm_client.base_url}")
    print(f"密钥已读取: {bool(platform._llm_client.api_key)}")

    # 只测工具循环本身，避免依赖 Redis/Qdrant 的真实会话状态。
    from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream
    from ai.agents.AiDiagnosisPlatform.search_tool import SEARCH_KB_SCHEMA
    from ai.agents.AiDiagnosisPlatform.ticket_tool import TOOL_SCHEMA

    search_count = 0
    async def search(_args):
        nonlocal search_count
        search_count += 1
        return {"content": "检索结果：相关度较低，暂无强相关操作文档。", "details": {"status": "ok"}, "terminate": False}

    async def submit(args):
        return {"content": "字段不足", "details": {"status": "collecting"}, "terminate": False}

    messages = [
        {"role": "system", "content": "你是AGV诊断助手。用户询问：任务一直路径规划中怎么办？请先检索知识库，再给出有界中文分析。"},
        {"role": "user", "content": "任务一直路径规划中怎么办？"},
    ]
    events = []
    async for ev in run_tool_loop_stream(
        platform._llm_client, messages, [SEARCH_KB_SCHEMA, TOOL_SCHEMA],
        {"search_kb": search, "submit_ticket": submit}, max_iterations=8,
    ):
        events.append(ev)
        if ev.get("event") == "token":
            print(ev.get("data", ""), end="", flush=True)

    done = next((e for e in events if e.get("event") == "done"), {})
    print("\n--- 测试结果 ---")
    print("检索次数:", search_count)
    print("工具结果数:", len(done.get("tool_results", [])))
    print("工具名称:", [r.get("name") for r in done.get("tool_results", [])])
    print("最终文本长度:", len(done.get("final_text", "")))
    print("最终文本:", done.get("final_text", "")[:300])


if __name__ == "__main__":
    asyncio.run(main())
