"""DeepSeek 思考模式 + 工具调用 reasoning_content 回传实测。

    python ai/tests/verify_tool_reasoning_roundtrip.py

官方文档：请求携带 tools 且思考开启时，后续轮必须回传中间 assistant 的
reasoning_content，否则 API 返回 400。本脚本用真实 API 验证：
  R1 两轮工具调用，assistant 回传【不带】reasoning_content → 观察 400
  R2 两轮工具调用，assistant 回传【带】reasoning_content  → 观察成功
"""
import asyncio, io, json, os, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))

from ai.core.llm import LLMClient, ServiceUnavailableError

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询城市天气",
        "parameters": {"type": "object", "properties": {
            "location": {"type": "string"}}, "required": ["location"]},
    },
}]


async def round_trip(c: LLMClient, pass_reasoning: bool):
    messages = [{"role": "user", "content": "杭州明天的天气怎么样？"}]

    # 轮 1：期望模型调工具
    r1 = await c.complete_with_tools(tools=TOOLS, messages=messages, thinking=True,
                                     max_tokens=400)
    msg1 = r1["raw"]["choices"][0]["message"]
    rc = msg1.get("reasoning_content", "")
    print(f"  轮1: tool_calls={[t['name'] for t in r1['tool_calls']]} "
          f"reasoning={len(rc)}字")

    # 回传 assistant（按官方样例 append 完整 message），再补 tool 结果
    assistant = {"role": "assistant", "content": msg1.get("content") or None,
                 "tool_calls": [{"id": t["id"], "type": "function",
                                 "function": {"name": t["name"],
                                              "arguments": json.dumps(t["arguments"], ensure_ascii=False)}}
                                for t in r1["tool_calls"]]}
    if pass_reasoning and rc:
        assistant["reasoning_content"] = rc
    messages.append(assistant)
    messages.append({"role": "tool", "tool_call_id": r1["tool_calls"][0]["id"],
                     "content": "多云 7~13°C"})

    # 轮 2：带工具结果求最终回答
    r2 = await c.complete_with_tools(tools=TOOLS, messages=messages, thinking=True,
                                     max_tokens=400)
    return (r2.get("content") or "").strip()


async def main():
    c = LLMClient(model="deepseek-v4-flash", reasoning_effort="low")

    print("R1 两轮工具调用，【不】回传 reasoning_content：")
    try:
        out = await round_trip(c, pass_reasoning=False)
        print(f"  结果: 成功（未 400）  回答: {out[:80]}")
        r1_400 = False
    except ServiceUnavailableError as e:
        print(f"  结果: 失败 → {str(e)[:200]}")
        r1_400 = True

    print("\nR2 两轮工具调用，【带】reasoning_content 回传：")
    try:
        out = await round_trip(c, pass_reasoning=True)
        print(f"  结果: 成功  回答: {out[:80]}")
        r2_ok = True
    except ServiceUnavailableError as e:
        print(f"  结果: 失败 → {str(e)[:200]}")
        r2_ok = False

    print("\n═══ 结论 ═══")
    print(f"  不回传 reasoning: {'400（官方文档命中，tool_loop.py 需修）' if r1_400 else '未 400（deepseek 实际宽容）'}")
    print(f"  回传 reasoning:   {'正常' if r2_ok else '失败（需再查）'}")


asyncio.run(main())
