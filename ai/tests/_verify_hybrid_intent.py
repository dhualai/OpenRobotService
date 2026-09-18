# -*- coding: utf-8 -*-
"""临时验证：混合意图判定（关键词三态 + LLM 兜底）"""
import asyncio
import importlib.util as _ilu
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_project_root = Path(__file__).resolve().parent.parent.parent
_dap_dir = _project_root / "ai" / "agents" / "AiDataAnalysisPlatform"


def _load_submodule(pkg_name: str, path: Path):
    spec = _ilu.spec_from_file_location(pkg_name, str(path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    return mod


# stub ai 包结构
import types
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

agent_mod = _load_submodule(
    "ai.agents.AiDataAnalysisPlatform.agent", _dap_dir / "agent.py"
)
DataAnalysisAgent = agent_mod.DataAnalysisAgent

# ── 1. 关键词三态 ──
cases = [
    ("你好", "chat"),
    ("谢谢啦", "unknown"),  # 礼貌正则不覆盖「啦」后缀 → LLM 兜底判 chat
    ("最近7天工单解决率怎么样？", "analysis"),
    ("帮我分析一下项目风险", "analysis"),
    ("今天天气不错，你在干嘛", "unknown"),
    ("近7天", "unknown"),  # 强模式命中但无动作/主体词 → 交给 LLM
    ("能不能帮我个忙", "unknown"),
]
print("── 关键词三态分类 ──")
ok = True
for q, expect in cases:
    got = DataAnalysisAgent._classify_question_intent(q)
    mark = "✓" if got == expect else "✗"
    if got != expect:
        ok = False
    print(f"  {mark} {q!r} → {got!r} (期望 {expect!r})")


# ── 2. LLM 兜底判定 ──
class FakeLLM:
    model_name = "fake"

    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    async def chat(self, system_prompt, user_prompt, **kw):
        self.calls.append((system_prompt, user_prompt, kw))
        return self._reply, None


class _FakeAgent:
    _llm = None


async def run_llm_cases():
    print("\n── LLM 兜底判定 ──")
    ok2 = True
    agent = object.__new__(DataAnalysisAgent)

    # 回复含 analysis
    agent._llm = FakeLLM("analysis")
    got = await agent._classify_intent_with_llm("今天天气不错，你在干嘛")
    mark = "✓" if got == "analysis" else "✗"
    ok2 = ok2 and got == "analysis"
    print(f"  {mark} LLM 回复 'analysis' → {got!r} (期望 analysis)")

    # 回复含 chat
    agent._llm = FakeLLM("chat")
    got = await agent._classify_intent_with_llm("今天天气不错，你在干嘛")
    mark = "✓" if got == "chat" else "✗"
    ok2 = ok2 and got == "chat"
    print(f"  {mark} LLM 回复 'chat' → {got!r} (期望 chat)")

    # 回复无法解析 → 回退 chat
    agent._llm = FakeLLM("随便说点什么")
    got = await agent._classify_intent_with_llm("今天天气不错，你在干嘛")
    mark = "✓" if got == "chat" else "✗"
    ok2 = ok2 and got == "chat"
    print(f"  {mark} LLM 回复不可解析 → {got!r} (期望回退 chat)")

    # LLM 异常 → 回退 chat
    class BoomLLM:
        model_name = "boom"

        async def chat(self, *a, **kw):
            raise RuntimeError("LLM down")

    agent._llm = BoomLLM()
    got = await agent._classify_intent_with_llm("今天天气不错，你在干嘛")
    mark = "✓" if got == "chat" else "✗"
    ok2 = ok2 and got == "chat"
    print(f"  {mark} LLM 异常 → {got!r} (期望回退 chat)")

    # 判定调用参数：低温度短回答
    fake = FakeLLM("chat")
    agent._llm = fake
    await agent._classify_intent_with_llm("今天天气不错，你在干嘛", context="闲聊")
    kw = fake.calls[0][2]
    mark = "✓" if kw.get("temperature") == 0 and kw.get("max_tokens") == 16 else "✗"
    ok2 = ok2 and kw.get("temperature") == 0 and kw.get("max_tokens") == 16
    print(f"  {mark} 判定调用参数 temperature=0/max_tokens=16 → {kw}")
    return ok2


ok2 = asyncio.run(run_llm_cases())

print(f"\n{'=' * 50}")
print("关键词三态:", "PASS" if ok else "FAIL")
print("LLM 兜底:", "PASS" if ok2 else "FAIL")
sys.exit(0 if (ok and ok2) else 1)
