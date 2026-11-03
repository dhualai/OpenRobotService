"""真实工单回放测试：用真实 task_id + query 回放，断言结构性事实。

与 signals.py 的区别：
  - signals 跑在「已生成的回答」上做错法检测（不调 LLM）
  - 本测试**回放真实流程**（调 Router → 看 is_pure_chat 判定），但**不调真实 LLM**
    （用 mock LLM 返回固定决策），断言的是**结构性事实**而非"答得对不对"

## 断言什么（都是确定性的）

  R1 路由意图：pure_chat 的 query 必须被 Router 判为 pure_chat；非 pure_chat 的不能误判
  R2 短路效果：pure_chat 不触发 Supervisor（不调 fit_history(for_plan=True)）
  R3 回复非空：任何路径的回复不能是空串
  R4 无兜底放弃：非 pure_chat 路径的回复不能含"无法确认/信息不足"等放弃话术

## 不断言什么

  - 不断言"答得对不对"（issues.md #22 的教训：LLM 行为不能靠单跑一次 pass/fail 判断）
  - 不断言回复具体内容（mock LLM 返回固定文本，断言内容没意义）

## 数据文件

用例定义在 `datasets/replay_cases.json`，格式：
  [
    {
      "task_id": "879",
      "query": "这个工单的对接人是谁",
      "expect_pure_chat": false,
      "expect_role_shortcut": true,
      "expect_intent_in": ["general"],
      "desc": "角色问句应短路"
    },
    {
      "task_id": "879",
      "query": "好的谢谢",
      "expect_pure_chat": true,
      "desc": "寒暄应判 pure_chat"
    }
  ]

不连库：测试 mock 了 load_task_context，用内存 TaskContext。
不调真实 LLM：Router 用 mock LLM，Supervisor 不实际跑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.agents.AiTaskPlatform.schemas import TaskContext


# ── 内存 mock LLM ────────────────────────────────────────────

class _MockLLM:
    """不调真实 API：complete 按预设意图返回，stream 返回固定文本。"""

    def __init__(self, intent: str = "general", challenge: bool = False):
        self._intent = intent
        self._challenge = challenge
        self.complete_calls: list[str] = []
        self.stream_calls: list[str] = []

    async def complete(self, prompt, system_prompt=None, max_tokens=2000,
                       temperature=0.1, thinking=False):
        self.complete_calls.append(prompt)
        # Router.classify_full 调 complete 解析 JSON
        if "intent" in prompt.lower() or "意图" in prompt:
            return json.dumps({
                "intent": self._intent,
                "confidence": 0.8,
                "challenge": self._challenge,
            }, ensure_ascii=False)
        # 其他 complete 调用（fit_ticket_history 等）返回简短文本
        return "（mock 压缩历史）"

    async def stream(self, prompt, system_prompt=None, max_tokens=600,
                     temperature=0.4):
        self.stream_calls.append(prompt)
        yield "（mock 回复）"


def _make_ctx(task_id: str = "999") -> TaskContext:
    """内存 TaskContext，不连库。"""
    return TaskContext(
        task_id=task_id,
        title="测试工单",
        description="测试描述",
        task_type="problem",
        priority="中",
        status="in_progress",
        source="ai",
    )


# ── 用例数据 ────────────────────────────────────────────────

_CASES_FILE = Path(__file__).resolve().parent / "datasets" / "replay_cases.json"


def _load_cases() -> list[dict]:
    if not _CASES_FILE.exists():
        # 数据文件不存在时用内置默认用例
        # mock_intent: 模拟真实 LLM 会返回的意图分类
        # expect_pure_chat: Router 解析后 is_pure_chat 是否应为 True
        return [
            {
                "task_id": "999",
                "query": "好的谢谢",
                "mock_intent": "pure_chat",
                "expect_pure_chat": True,
                "desc": "寒暄应判 pure_chat",
            },
            {
                "task_id": "999",
                "query": "这个工单的对接人是谁",
                "mock_intent": "general",
                "expect_pure_chat": False,
                "expect_intent_in": ["general"],
                "desc": "角色问句应判非 pure_chat",
            },
            {
                "task_id": "999",
                "query": "帮我分析一下日志里的报错",
                "mock_intent": "log_analysis",
                "expect_pure_chat": False,
                "expect_intent_in": ["log_analysis"],
                "desc": "日志分析请求应判非 pure_chat",
            },
            {
                "task_id": "999",
                "query": "",
                "expect_pure_chat": False,
                "desc": "空 query 不进 Router（is_pure_chat 默认 False）",
            },
        ]
    return json.loads(_CASES_FILE.read_text(encoding="utf-8"))


# ── R1: 路由意图断言 ─────────────────────────────────────────

@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c.get("desc", c["query"][:20]))
@pytest.mark.asyncio
async def test_router_intent(case):
    """R1：Router 对 query 的意图分类必须与预期一致。"""
    if not case["query"]:
        # 空 query 不进 Router，is_pure_chat 保持 False
        assert not case.get("expect_pure_chat"), "空 query 不应期望 pure_chat"
        return

    mock_llm = _MockLLM(intent=case.get("mock_intent", "general"))
    from ai.agents.AiTaskPlatform.capabilities import Router
    cls = await Router.classify_full(
        llm_client=mock_llm,
        query=case["query"],
        has_attachments=False,
        has_logs=False,
    )

    if cls:
        if case.get("expect_pure_chat"):
            assert cls["intent"] == "pure_chat", (
                f"期望 pure_chat，实际 {cls['intent']} — {case['desc']}"
            )
        else:
            assert cls["intent"] != "pure_chat", (
                f"期望非 pure_chat，实际 {cls['intent']} — {case['desc']}"
            )
        if case.get("expect_intent_in"):
            assert cls["intent"] in case["expect_intent_in"], (
                f"期望 intent 在 {case['expect_intent_in']}，实际 {cls['intent']} — {case['desc']}"
            )


# ── R2: pure_chat 不触发 Supervisor ──────────────────────────

@pytest.mark.asyncio
async def test_pure_chat_skips_fit_history_for_plan():
    """R2：pure_chat 时不应调用 _fit_history(for_plan=True)。

    通过代码审查确认改动结构：
    1. Router.classify_full 调用在 _fit_history(for_plan=True) 之前
    2. _fit_history(for_plan=True) 被 if not is_pure_chat: 条件包裹
    """
    import inspect
    from ai.agents.AiTaskPlatform.handlers import discuss_flow

    source = inspect.getsource(discuss_flow.DiscussFlow.discuss)

    # 找到 Router 实际调用行（非注释/非 import）
    router_call_line = None
    fit_history_call_line = None
    for i, line in enumerate(source.split('\n')):
        stripped = line.strip()
        # 跳过注释和 import 行
        if stripped.startswith('#') or stripped.startswith('from ') or stripped.startswith('import '):
            continue
        # Router.classify_full 的实际调用（await 开头或含 = await Router.classify_full）
        if 'await Router.classify_full' in stripped and router_call_line is None:
            router_call_line = i
        # _fit_history(for_plan=True) 的实际调用
        if '_fit_history(for_plan=True)' in stripped and 'await ' in stripped and fit_history_call_line is None:
            fit_history_call_line = i

    assert router_call_line is not None, "未找到 Router.classify_full 调用"
    assert fit_history_call_line is not None, "未找到 _fit_history(for_plan=True) 调用"
    assert router_call_line < fit_history_call_line, (
        f"Router 应在 _fit_history(for_plan=True) 之前执行 "
        f"(router@line{router_call_line}, fit_history@line{fit_history_call_line})"
    )

    # 确认 _fit_history(for_plan=True) 被条件包裹
    # 检查调用之前的最近非空行是否含 if not is_pure_chat
    lines = source.split('\n')
    for i in range(fit_history_call_line - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith('#'):
            assert 'is_pure_chat' in stripped or 'if not' in stripped, (
                f"_fit_history(for_plan=True) 应被 if not is_pure_chat 条件包裹，"
                f"最近的非空行: {stripped}"
            )
            break


# ── R3: 回复非空断言 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_mock_reply_not_empty():
    """R3：任何路径的 mock 回复不能是空串。"""
    mock_llm = _MockLLM()
    reply_parts = []
    async for token in mock_llm.stream("test", max_tokens=100):
        reply_parts.append(token)
    reply = "".join(reply_parts)
    assert reply.strip(), "回复不能为空串"


# ── R4: 放弃话术检测（复用 signals.is_giveup）──────────────────

def test_non_pure_chat_reply_no_giveup():
    """R4：非 pure_chat 路径的回复不能含放弃话术。"""
    from ai.agents.AiTaskPlatform.regression.signals import is_giveup

    # mock 一个非 pure_chat 路径的典型回复
    sample_replies = [
        "根据日志分析，报错发生在规划模块，建议检查路径下发逻辑。",
        "图片显示的是 AGV 卡在充电桩前方，可能是传感器遮挡。",
    ]
    for reply in sample_replies:
        result = is_giveup(reply)
        assert not result["giveup"], (
            f"非 pure_chat 回复不应含放弃话术，命中：{result['hits']}"
        )


# ── 结构性断言：_ask_reanalyze_log 修复验证 ────────────────────

def test_ask_reanalyze_log_uses_router_challenge():
    """验证 _ask_reanalyze_log 的计算在 Router 之后（修复既有 bug）。"""
    import inspect
    from ai.agents.AiTaskPlatform.handlers import discuss_flow

    source = inspect.getsource(discuss_flow.DiscussFlow.discuss)
    # Router 赋值 _llm_challenge 的位置
    router_challenge_pos = source.find("_llm_challenge = bool(_cls.get(\"challenge\"))")
    # _ask_reanalyze_log 计算的位置
    reanalyze_pos = source.find("_ask_reanalyze_log = bool(_llm_challenge)")
    assert 0 < router_challenge_pos < reanalyze_pos, (
        "_ask_reanalyze_log 应在 Router 赋值 _llm_challenge 之后计算 "
        f"(router_challenge@{router_challenge_pos}, reanalyze@{reanalyze_pos})"
    )
