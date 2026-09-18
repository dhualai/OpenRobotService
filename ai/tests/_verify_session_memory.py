#!/usr/bin/env python3
"""会话记忆与聊天人设验证：LLMClient session_id 复用 + agent conversation_id 贯通

覆盖本次「阶段一：持久 session_id + 人设拆分」的新增行为：
1. LLMClient._build_payload：未传 session_id → 生成唯一 ID；传入 → 复用
2. agent.chat 普通聊天分支：回传 conversation_id 且作为 session_id 透传 LLM
3. agent.chat_stream 聊天分支：meta/done 携带同一 conversation_id
4. 聊天人设切换：chat 分支使用自由对话人设（非数据分析专家人设）
5. 指标分析透传：_analyze_by_plan 将 conversation_id 作为 session_id 传给分析引擎

用法：
    python ai/tests/_verify_session_memory.py

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
                 "report_prompts", "report_generator", "agent"]:
        mods[name] = _load_submodule(
            f"ai.agents.AiDataAnalysisPlatform.{name}", _dap_dir / f"{name}.py",
        )
    return mods


mods = _stub_load_dap()
agent_mod = mods["agent"]
llm_mod = mods["llm_client"]


class FakeCfg:
    api_base_url = "http://127.0.0.1:9999"

    class Settings:
        timeout = 30
        temperature = 0.7
        max_tokens = 2000

    settings = Settings()

    class PC:
        model = "mock"

    provider_config = PC()


class SpyLLM:
    """记录 chat/chat_stream 调用参数（含 session_id），返回固定文本。"""

    model_name = "spy-model"

    def __init__(self):
        self.calls: list[dict] = []

    async def chat(self, system_prompt, user_prompt, **kw):
        self.calls.append(kw)
        return "你好，有什么可以帮你？", None

    async def chat_stream(self, system_prompt, user_prompt, **kw):
        self.calls.append(kw)
        yield "你"
        yield "好"


class SpyAnalyzer:
    """记录 analyze 调用参数，验证 session_id 透传。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def analyze(self, **kw):
        self.calls.append(kw)
        from ai.agents.AiDataAnalysisPlatform.schemas import AnalysisResult
        return AnalysisResult(
            analysis_type=kw.get("analysis_type"),
            summary="mock",
            raw_response="mock",
        )

    async def analyze_stream(self, **kw):
        raise AssertionError("本验证不触发流式分析")


class SpyAgent(agent_mod.DataAnalysisAgent):
    def __init__(self, llm=None):
        self._llm = llm or SpyLLM()
        self._analyzer = SpyAnalyzer()
        self._planner = mods["metric_planner"].AnalysisPlanner(self._llm)
        self._plan_cache = mods["metric_planner"].PlanConversationCache()


def main() -> None:
    # 1) LLMClient._build_payload 的 session_id 复用/回退
    client = llm_mod.LLMClient(FakeCfg())
    p1 = client._build_payload("sys", "u", None, None)
    assert p1["session_id"].startswith("analysis-"), p1
    p2 = client._build_payload("sys", "u", None, None, session_id="conversation-abc")
    assert p2["session_id"] == "conversation-abc", p2
    p3 = client._build_payload("sys", "u", None, None, session_id="")
    assert p3["session_id"].startswith("analysis-"), p3
    print("✓ llm_client session_id 复用/回退")

    # 2) agent.chat 普通聊天分支：conversation_id 回传 + session_id 透传
    spy = SpyLLM()
    a = SpyAgent(llm=spy)
    r = asyncio.run(a.chat("你好"))
    assert r.mode == "chat" and r.conversation_id, r
    assert spy.calls[-1].get("session_id") == r.conversation_id, spy.calls[-1]
    print(f"✓ chat 分支 conversation_id 回传 + session_id 透传 ({r.conversation_id[:12]}…)")

    # 3) 聊天人设切换：自由对话人设，不再是数据分析专家人设
    chat_sp = mods["prompts"].build_chat_system_prompt()
    assert "智能助手" in chat_sp and "数据分析专家" not in chat_sp, chat_sp[:80]
    assert "固定标题模板" in chat_sp  # 放宽格式约束的声明
    print("✓ 聊天人设切换（自由对话人设）")

    # 4) agent.chat_stream 聊天分支：meta/done 携带同一 conversation_id
    spy2 = SpyLLM()
    a2 = SpyAgent(llm=spy2)
    evts = []

    async def collect():
        async for e in a2.chat_stream("你好"):
            evts.append(e)

    asyncio.run(collect())
    meta = next(e for e in evts if e["type"] == "meta")
    done = next(e for e in evts if e["type"] == "done")
    assert meta["mode"] == "chat" and meta["conversation_id"], meta
    assert done["conversation_id"] == meta["conversation_id"]
    assert spy2.calls[-1].get("session_id") == meta["conversation_id"]
    assert all(e["type"] in ("meta", "delta", "done") for e in evts)
    print("✓ chat_stream 分支 conversation_id 回传 + session_id 透传")

    # 5) 指标流程透传链：_analyze_by_plan → analyzer.analyze(_stream) 支持 session_id
    # （采集依赖 DB 不实际调用，仅验证签名链路）
    import inspect

    sig = inspect.signature(agent_mod.DataAnalysisAgent._analyze_by_plan)
    assert "conversation_id" in sig.parameters
    sig2 = inspect.signature(mods["analyzer"].DataAnalyzer.analyze)
    assert "session_id" in sig2.parameters
    sig3 = inspect.signature(mods["analyzer"].DataAnalyzer.analyze_stream)
    assert "session_id" in sig3.parameters
    print("✓ _analyze_by_plan / analyzer 签名支持 session_id 透传")

    print("\nALL SESSION-MEMORY CHECKS PASSED")


if __name__ == "__main__":
    main()
