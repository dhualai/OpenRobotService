#!/usr/bin/env python3
"""Agentic 自由对话（阶段二）验证：ReAct 循环、工具调用、降级

覆盖本次「阶段二：工具调用 Agent」的新增行为：
1. 闲聊不调工具 → chat 模式自由回答 + 最终问答对显式落库
2. 数据问题 → query_metrics → analysis 模式 + charts/cards 下发
3. 项目消歧 → list_projects → query_metrics 两轮工具接力
4. 循环上限：连续工具调用最多 _MAX_AGENTIC_TOOL_ROUNDS 轮后兜底回答
5. LLM 客户端无工具能力 → 自动降级 chat_stream（零回归）
6. messages 回填格式（assistant tool_calls + tool 结果）与历史注入

用法：
    python ai/tests/_verify_agentic_chat.py

说明：与 eval_metric_e2e.py 相同的 stub 加载方式，零外部依赖、零网络调用。
"""
from __future__ import annotations

import asyncio
import importlib.util as _ilu
import sys
import types
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
_dap_dir = _project_root / "ai" / "agents" / "AiDataAnalysisPlatform"


def _load_submodule(pkg_name: str, path: Path):
    spec = _ilu.spec_from_file_location(pkg_name, str(path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub_load_dap() -> dict:
    ai_pkg = types.ModuleType("ai")
    ai_pkg.__path__ = [str(_project_root / "ai")]
    agents_pkg = types.ModuleType("ai.agents")
    agents_pkg.__path__ = [str(_project_root / "ai" / "agents")]
    dap_pkg = types.ModuleType("ai.agents.AiDataAnalysisPlatform")
    dap_pkg.__path__ = [str(_dap_dir)]
    sys.modules.setdefault("ai", ai_pkg)
    sys.modules.setdefault("ai.agents", agents_pkg)
    sys.modules.setdefault("ai.agents.AiDataAnalysisPlatform", dap_pkg)

    ai_core_pkg = types.ModuleType("ai.core")
    ai_core_pkg.__path__ = [str(_project_root / "ai" / "core")]
    sys.modules.setdefault("ai.core", ai_core_pkg)
    _load_submodule("ai.core.database", _project_root / "ai" / "core" / "database.py")

    mods = {}
    for name in ["logging_config", "metric_registry", "report_schemas", "schemas",
                 "prompts", "metric_planner", "config", "llm_client", "analyzer",
                 "report_prompts", "report_generator", "chart_builder", "tools",
                 "agent"]:
        mods[name] = _load_submodule(
            f"ai.agents.AiDataAnalysisPlatform.{name}", _dap_dir / f"{name}.py",
        )
    return mods


mods = _stub_load_dap()
agent_mod = mods["agent"]


class SpyAnalyzer:
    async def analyze(self, **kw):
        raise AssertionError("agentic 验证不触发非流式分析")

    async def analyze_stream(self, **kw):
        raise AssertionError("agentic 验证不触发流式分析")


class SpyAgent(agent_mod.DataAnalysisAgent):
    def __init__(self, llm=None):
        self._llm = llm
        self._analyzer = SpyAnalyzer()
        self._planner = mods["metric_planner"].AnalysisPlanner(self._llm)
        self._plan_cache = mods["metric_planner"].PlanConversationCache()


class SpyAgenticLLM:
    """按 script 依次返回 chat_with_tools 结果，记录全部调用与落库。"""

    model_name = "spy-agentic-model"

    def __init__(self, script: list[dict], history: list[dict] | None = None):
        self.script = script
        self.history = history or []
        self.calls: list[dict] = []
        self.saved_turns: list[tuple] = []

    async def chat_with_tools(self, system_prompt, user_prompt, tools, *,
                              messages=None, session_id=None, temperature=None,
                              max_tokens=None, save_memory=True):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tools": tools,
            "messages": [dict(m) for m in (messages or [])],
            "session_id": session_id,
            "save_memory": save_memory,
        })
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        return dict(self.script[idx])

    async def fetch_history(self, session_id: str) -> list[dict]:
        return [dict(h) for h in self.history]

    async def add_turn(self, session_id: str, role: str, content: str) -> None:
        self.saved_turns.append((session_id, role, content))


class SpyPlainLLM:
    """无 chat_with_tools 能力（模拟旧服务/测试 Mock），验证降级路径。"""

    model_name = "spy-plain-model"

    def __init__(self):
        self.calls: list[dict] = []

    async def chat(self, system_prompt, user_prompt, **kw):
        self.calls.append(kw)
        return "你好，有什么可以帮你？", None

    async def chat_stream(self, system_prompt, user_prompt, **kw):
        self.calls.append(kw)
        yield "你"
        yield "好"


async def collect(agent, **kw) -> list[dict]:
    evts = []
    async for e in agent.agentic_chat_stream(**kw):
        evts.append(e)
    return evts


def _joined(evts: list[dict]) -> str:
    return "".join(e["content"] for e in evts if e["type"] == "delta")


def main() -> None:
    # 替换 execute_tool 为 spy（agent 模块内引用，替换模块属性即生效）
    tool_calls_log: list[tuple] = []
    _orig = agent_mod.execute_tool

    async def _fake_execute_tool(name, arguments, agent, user_id=None):
        tool_calls_log.append((name, dict(arguments or {})))
        if name == "list_projects":
            return {"text": "候选：罗勇项目（P001，0.98）", "candidates": ["罗勇项目"]}
        if name == "query_metrics":
            return {
                "text": "统计周期：近7天；范围：全部项目。",
                "charts": [{"type": "bar", "title": "mock-chart"}],
                "cards": [{"title": "mock-card"}],
            }
        return {"text": "未知工具"}

    agent_mod.execute_tool = _fake_execute_tool

    # 1) 闲聊不调工具 → chat 模式 + 历史注入 + 最终问答对落库
    llm = SpyAgenticLLM(
        script=[{"content": "你好呀！想聊点什么？", "tool_calls": []}],
        history=[
            {"role": "user", "content": "你是谁"},
            {"role": "assistant", "content": "我是智能助手"},
        ],
    )
    a = SpyAgent(llm=llm)
    evts = asyncio.run(collect(a, question="你好"))
    meta, done = evts[0], evts[-1]
    assert meta["type"] == "meta" and meta["mode"] == "chat", meta
    assert meta["charts"] is None and meta["cards"] is None
    assert _joined(evts) == "你好呀！想聊点什么？"
    assert done["type"] == "done" and done["conversation_id"] == meta["conversation_id"]
    c0 = llm.calls[0]
    assert c0["system_prompt"] == "" and c0["save_memory"] is False
    assert [m["role"] for m in c0["messages"]] == ["system", "user", "assistant", "user"]
    assert c0["messages"][0]["content"]  # system 非空（AGENTIC 人设+指标清单）
    assert c0["messages"][1]["content"] == "你是谁"
    assert c0["messages"][-1]["content"] == "你好"
    assert llm.saved_turns == [
        (meta["conversation_id"], "user", "你好"),
        (meta["conversation_id"], "assistant", "你好呀！想聊点什么？"),
    ]
    print("✓ 闲聊：chat 模式 + 历史注入 + 问答对落库")

    # 2) 数据问题 → query_metrics → analysis 模式 + charts/cards + messages 回填
    llm2 = SpyAgenticLLM(script=[
        {"content": "", "tool_calls": [{
            "id": "t1", "name": "query_metrics",
            "arguments": {"metric_keys": ["ticket.resolve_rate"]},
        }]},
        {"content": "近7天工单解决率约 82%，整体稳定。", "tool_calls": []},
    ])
    a2 = SpyAgent(llm=llm2)
    tool_calls_log.clear()
    evts2 = asyncio.run(collect(a2, question="最近工单解决率怎么样"))
    meta2 = evts2[0]
    assert meta2["mode"] == "analysis", meta2
    assert len(meta2["charts"]) == 1 and len(meta2["cards"]) == 1
    assert _joined(evts2) == "近7天工单解决率约 82%，整体稳定。"
    assert [n for n, _ in tool_calls_log] == ["query_metrics"]
    c1 = llm2.calls[1]
    roles = [m["role"] for m in c1["messages"]]
    assert roles == ["system", "user", "assistant", "tool"], roles
    assert c1["messages"][2]["tool_calls"][0]["id"] == "t1"
    assert c1["messages"][3]["tool_call_id"] == "t1"
    assert c1["messages"][3]["content"].startswith("统计周期")
    assert llm2.saved_turns[-1][2] == "近7天工单解决率约 82%，整体稳定。"
    print("✓ 指标查询：query_metrics + analysis 模式 + charts/cards + 回填格式")

    # 3) 项目消歧 → list_projects → query_metrics 两轮工具接力
    llm3 = SpyAgenticLLM(script=[
        {"content": "", "tool_calls": [{
            "id": "c1", "name": "list_projects", "arguments": {"name_hint": "罗勇"},
        }]},
        {"content": "", "tool_calls": [{
            "id": "c2", "name": "query_metrics",
            "arguments": {"metric_keys": ["ticket.count"], "project_code": "P001"},
        }]},
        {"content": "罗勇项目近7天共 12 个工单。", "tool_calls": []},
    ])
    a3 = SpyAgent(llm=llm3)
    tool_calls_log.clear()
    evts3 = asyncio.run(collect(a3, question="罗勇项目最近工单多吗"))
    assert evts3[0]["mode"] == "analysis"
    assert len(llm3.calls) == 3
    assert [(n, args) for n, args in tool_calls_log] == [
        ("list_projects", {"name_hint": "罗勇"}),
        ("query_metrics", {"metric_keys": ["ticket.count"], "project_code": "P001"}),
    ]
    print("✓ 项目消歧：list_projects → query_metrics 两轮接力")

    # 4) 循环上限：连续工具调用最多 _MAX_AGENTIC_TOOL_ROUNDS 轮后兜底
    llm4 = SpyAgenticLLM(script=[{
        "content": "", "tool_calls": [{
            "id": "x1", "name": "query_metrics",
            "arguments": {"metric_keys": ["ticket.count"]},
        }],
    }])
    a4 = SpyAgent(llm=llm4)
    tool_calls_log.clear()
    evts4 = asyncio.run(collect(a4, question="查数据"))
    max_rounds = agent_mod._MAX_AGENTIC_TOOL_ROUNDS
    assert len(llm4.calls) == max_rounds + 1, len(llm4.calls)
    assert len(tool_calls_log) == max_rounds + 1
    assert evts4[0]["mode"] == "analysis"
    assert _joined(evts4).startswith("抱歉")
    print(f"✓ 循环上限：{max_rounds}+1 轮后兜底回答")

    # 5) 无工具能力 → 自动降级 chat_stream（零回归）
    llm5 = SpyPlainLLM()
    a5 = SpyAgent(llm=llm5)
    evts5 = asyncio.run(collect(a5, question="你好"))
    meta5 = evts5[0]
    assert meta5["type"] == "meta" and meta5["mode"] == "chat", meta5
    assert meta5["conversation_id"]
    assert _joined(evts5) == "你好"
    assert all(e["type"] in ("meta", "delta", "done") for e in evts5)
    print("✓ 无工具能力：自动降级 chat_stream")

    # 恢复现场（脚本进程内无后续用例，保留也无妨）
    agent_mod.execute_tool = _orig

    print("\nALL AGENTIC CHECKS PASSED")


if __name__ == "__main__":
    main()
