"""验证 run_tool_loop_stream 的流式行为（不依赖真实 LLM）。

关键验证点（防回归）：
1. 工具调用轮的过渡正文**随到随发**（绝不攒到整轮流结束再回放——否则前端卡几秒
   突然一坨 = 非流式，实测用户反馈「先出一个字再卡」）
2. 纯文本轮正文正常逐 token 透传
3. 哨兵事件 content/tool_calls 正确
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream  # noqa: E402


class FakeLLM:
    """模拟 stream_with_tools：固定两轮。

    round 1: 输出过渡正文「好的，已记录」+ tool_calls=[submit_ticket]
    round 2: 输出纯正文「工单还差最后一项：地图信息」
    """
    def __init__(self):
        self.calls = 0

    async def stream_with_tools(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            toks = ["好的，已记录", "下发场景。", "那还差最后一项", "——地图信息。"]
            for t in toks:
                yield {"type": "token", "content": t}
            yield {"type": "tool_calls", "tool_calls": [
                {"id": "call_1", "name": "submit_ticket",
                 "arguments": {"problem_summary": "invalid order", "project": "x"}}],
                "content": "好的，已记录下发场景。那还差最后一项——地图信息。"}
        else:
            toks = ["我先帮您登记：工单还差最后一项", "——地图信息，麻烦补充一下。"]
            for t in toks:
                yield {"type": "token", "content": t}
            yield {"type": "tool_calls", "tool_calls": [],
                   "content": "我先帮您登记：工单还差最后一项——地图信息，麻烦补充一下。"}


class FakeTool:
    """submit_ticket 执行器：模拟「还缺字段 → 收集继续」。"""
    def __init__(self):
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        return {"content": "已登记，还缺地图信息", "details": {"status": "collecting"}, "terminate": False}


async def main():
    fake_llm = FakeLLM()
    fake_tool = FakeTool()

    messages = [{"role": "user", "content": "我要提单"}]
    tokens = []          # 收到的 token 事件
    done = None
    async for ev in run_tool_loop_stream(fake_llm, messages, [{"dummy": True}], {"submit_ticket": fake_tool}):
        if ev["event"] == "token":
            tokens.append(ev["data"])
        elif ev["event"] == "done":
            done = ev

    streamed = "".join(tokens)
    print("=== 验证 ===")
    print(f"LLM 调用次数: {fake_llm.calls} (期望 2)")
    print(f"工具调用次数: {len(fake_tool.calls)} (期望 1)")
    print(f"token 事件数: {len(tokens)} (期望 ≥ 6，逐 token 透传不缓冲)")
    print(f"透传正文: {streamed!r}")
    print(f"done.final_text: {done['final_text']!r}")
    print(f"done.tool_results: {len(done['tool_results'])} 条")

    ok = True
    if fake_llm.calls != 2:
        print("❌ LLM 调用次数不对"); ok = False
    if len(tokens) < 6:
        print(f"❌ token 事件数过少（{len(tokens)}）——疑似攒缓冲后一次性回放"); ok = False
    else:
        print("✅ token 逐批透传，无缓冲")
    if "好的，已记录" not in streamed:
        print("❌ 过渡正文没透传（说明还在用缓冲抑制）"); ok = False
    else:
        print("✅ 工具调用轮过渡正文随到随发")
    if "我先帮您登记" not in streamed:
        print("❌ 纯文本轮正文没透传"); ok = False
    else:
        print("✅ 纯文本轮正文正常透传")
    if done["final_text"] != "我先帮您登记：工单还差最后一项——地图信息，麻烦补充一下。":
        print(f"❌ final_text 哨兵不对: {done['final_text']!r}"); ok = False
    else:
        print("✅ final_text 哨兵正确")

    print("\n=== " + ("PASS" if ok else "FAIL") + " ===")


if __name__ == "__main__":
    asyncio.run(main())
