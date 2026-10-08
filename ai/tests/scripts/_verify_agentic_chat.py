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

_project_root = Path(__file__).resolve().parent.parent.parent.parent
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
    """按 script 依次返回 chat_with_tools 结果，记录全部调用与落库。

    chat 方法（追问建议/历史摘要等辅助调用）返回 ``chat_reply`` 配置文本。
    """

    model_name = "spy-agentic-model"

    def __init__(self, script: list[dict], history: list[dict] | None = None,
                 chat_reply: str = "[]"):
        self.script = script
        self.history = history or []
        self.chat_reply = chat_reply
        self.calls: list[dict] = []
        self.saved_turns: list[tuple] = []
        self.chat_calls: list[tuple] = []

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

    async def chat(self, system_prompt, user_prompt, **kw):
        self.chat_calls.append((system_prompt, user_prompt, kw))
        return self.chat_reply, None


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
                "scope_title": "罗勇项目",
                "date_range": "2026-09-14 ~ 2026-09-20",
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
        chat_reply='["和上周相比呢？", "按项目拆分呢？"]',
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
    # 追问建议：done 附带 LLM 生成的建议
    assert done["suggest_questions"] == ["和上周相比呢？", "按项目拆分呢？"]
    assert llm.chat_calls and "助手回答" in llm.chat_calls[-1][1]
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
    # 数据卡头部元信息：项目名大标题 + 数据日期随 meta 下发
    assert meta2["scope_title"] == "罗勇项目", meta2
    assert meta2["date_range"] == "2026-09-14 ~ 2026-09-20", meta2
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

    # 6) reasoning 透传：首轮思考过程作为 reasoning 事件（meta 之前下发）
    llm6 = SpyAgenticLLM(script=[
        {"content": "", "reasoning": "用户问数据，先查近7天解决率",
         "tool_calls": [{"id": "r1", "name": "query_metrics",
                          "arguments": {"metric_keys": ["ticket.resolve_rate"]}}]},
        {"content": "近7天解决率 82%。", "reasoning": "", "tool_calls": []},
    ])
    a6 = SpyAgent(llm=llm6)
    evts6 = asyncio.run(collect(a6, question="最近解决率怎么样"))
    assert evts6[0]["type"] == "reasoning", evts6[0]
    assert evts6[0]["content"] == "用户问数据，先查近7天解决率"
    assert evts6[1]["type"] == "meta" and evts6[1]["mode"] == "analysis"
    print("✓ reasoning 事件透传（meta 之前下发）")

    # 7) 长会话历史压缩：超阈值时最旧轮次 LLM 摘要 + 保留最近轮次
    long_history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"历史消息{i}"}
        for i in range(10)
    ]
    llm7 = SpyAgenticLLM(
        script=[{"content": "基于历史，答案是稳定。", "tool_calls": []}],
        history=long_history,
        chat_reply="这是更早对话的摘要内容",
    )
    a7 = SpyAgent(llm=llm7)
    evts7 = asyncio.run(collect(a7, question="和之前比呢"))
    assert evts7[0]["type"] == "meta" and evts7[0]["mode"] == "chat"
    c7 = llm7.calls[0]
    msgs7 = c7["messages"]
    # system(人设) + system(摘要) + 最近4条 + 当前用户
    assert [m["role"] for m in msgs7] == [
        "system", "system", "user", "assistant", "user", "assistant", "user"
    ], [m["role"] for m in msgs7]
    assert msgs7[1]["content"].startswith("[更早对话摘要]"), msgs7[1]["content"][:40]
    assert msgs7[1]["content"].endswith("这是更早对话的摘要内容")
    assert msgs7[-1]["content"] == "和之前比呢"
    # 摘要调用使用了摘要提示词，且用户输入包含旧轮文本
    assert llm7.chat_calls and "历史消息" in llm7.chat_calls[0][1]
    print("✓ 长会话历史压缩：摘要注入 + 最近轮次保留")

    # 8) 追问建议生成失败 → 静默跳过（suggest_questions=None）
    llm8 = SpyAgenticLLM(
        script=[{"content": "你好！", "tool_calls": []}],
        chat_reply="抱歉，我无法生成建议。",
    )
    a8 = SpyAgent(llm=llm8)
    evts8 = asyncio.run(collect(a8, question="你好"))
    done8 = evts8[-1]
    assert done8["type"] == "done" and done8["suggest_questions"] is None
    print("✓ 追问建议解析失败 → 静默跳过")

    # 9) 默认范围规则：未指定项目时只查用户关联项目（不查全部项目）
    tools_mod = mods["tools"]
    rg_mod = mods["report_generator"]
    _orig_resolve = rg_mod.ReportGenerator._resolve_project_ids_by_user

    async def _check_scope(args, user_id):
        return await tools_mod._resolve_scope(args, None, user_id)

    rg_mod.ReportGenerator._resolve_project_ids_by_user = staticmethod(
        lambda uid: ["P001", "P002"]
    )
    ids, label = asyncio.run(_check_scope(
        {"metric_keys": ["project.total"]}, "u1"))
    assert ids == ["P001", "P002"], ids
    assert label == "用户关联项目（2 个）", label
    ids2, _ = asyncio.run(_check_scope(
        {"metric_keys": ["project.total"], "scope_type": "global"}, "u1"))
    assert ids2 == ["P001", "P002"], ids2
    ids3, _ = asyncio.run(_check_scope(
        {"metric_keys": ["project.total"]}, None))
    assert ids3 is None, ids3

    rg_mod.ReportGenerator._resolve_project_ids_by_user = staticmethod(
        lambda uid: []
    )
    ids4, label4 = asyncio.run(_check_scope(
        {"metric_keys": ["project.total"]}, "u1"))
    assert ids4 == ["__no_project__"] and "无关联项目" in label4, (ids4, label4)
    rg_mod.ReportGenerator._resolve_project_ids_by_user = _orig_resolve

    ids5, _ = asyncio.run(_check_scope(
        {"metric_keys": ["project.total"], "scope_type": "single_project",
         "project_code": "P009"}, "u1"))
    assert ids5 == ["P009"], ids5
    print("✓ 默认范围：未指定项目 → 仅用户关联项目；无 user_id 才查全部")

    # 工具描述规则文本：默认 user_projects
    desc = tools_mod.QUERY_METRICS_TOOL["function"]["description"]
    assert "user_projects" in desc and "同样只统计用户关联的项目" in desc
    agentic_prompt = mods["prompts"].build_agentic_system_prompt()
    assert "未提及项目时默认只查用户关联的项目" in agentic_prompt
    print("✓ 规则文本：工具描述 + 系统提示词默认用户关联项目")

    # 恢复现场（脚本进程内无后续用例，保留也无妨）
    agent_mod.execute_tool = _orig

    print("\nALL AGENTIC CHECKS PASSED")


if __name__ == "__main__":
    main()
