"""tool_loop reasoning_content 回传验证（真实入口：run_tool_loop_stream + deepseek 开思考）。

    python ai/tests/verify_tool_loop_reasoning.py
"""
import asyncio, io, json, os, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))

from ai.core.llm import LLMClient
from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询城市天气",
        "parameters": {"type": "object", "properties": {
            "location": {"type": "string"}}, "required": ["location"]},
    },
}]


async def main():
    llm = LLMClient(model="deepseek-v4-flash", reasoning_effort="low")
    messages = [{"role": "user", "content": "杭州明天的天气怎么样？"}]

    async def exec_weather(args):
        return {"content": "多云 7~13°C", "details": {"status": "ok"}, "terminate": False}

    final_text = ""
    async for ev in run_tool_loop_stream(llm, messages, TOOLS,
                                         {"get_weather": exec_weather}, thinking=True):
        if ev.get("event") == "token":
            pass  # 流式 token
        elif ev.get("event") == "done":
            final_text = ev.get("final_text") or ""

    print(f"最终回答: {final_text[:80]}")
    asst = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
    ok_reasoning = bool(asst) and bool(asst[0].get("reasoning_content"))
    print(f"assistant 回传消息数: {len(asst)}，"
          f"首条 reasoning_content: {'✓ ' + str(len(asst[0]['reasoning_content'])) + ' 字' if ok_reasoning else '✗ 缺失'}")
    print("\n═══ 结论 ═══")
    print("PASS：循环完成且回传了 reasoning_content" if ok_reasoning and final_text
          else "FAIL：见上方输出")
    sys.exit(0 if (ok_reasoning and final_text) else 1)


asyncio.run(main())
