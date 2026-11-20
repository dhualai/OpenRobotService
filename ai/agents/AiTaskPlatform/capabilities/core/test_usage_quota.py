"""能力调度配额：按请求计数、超限物理拦下（不是靠 prompt 求 LLM）。

回归背景：`max_usage_per_session` 从首个公测版本（f3cd531f, 2026-08-23）落地起**从未生效**——
计数器挂在能力实例上，而实例每次新建（`registry.get()` → `cap_cls()`），
`_claim_usage()` 每次看到的都是 0。见 AiTaskPlatform/issues.md #10。

现在由 supervisor 在机制层强制：计数挂请求（runtime_ctx）→ 超限不排进计划 / 不派发
→ 把「已达上限」写进汇总正文。本文件就锁这三件事。
"""

import asyncio

import pytest

from ai.agents.AiTaskPlatform.capabilities.core import usage
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult
from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
from ai.agents.AiTaskPlatform.capabilities.core.supervisor import Supervisor, SupervisorDecision
from ai.agents.AiTaskPlatform.capabilities.core.supervisor_todo import TodoList


class _LimitedStub(BaseCapability):
    """上限 2 次的替身；类计数器记录真实执行了几次。"""

    name = "quota_stub"
    description = "配额测试替身"
    max_usage_per_session = 2
    ran = 0

    async def run(self, **kwargs):
        type(self).ran += 1
        return CapabilityResult(text=f"第{type(self).ran}次")


class _UnlimitedStub(BaseCapability):
    name = "quota_stub_unlimited"
    description = "不限次数的替身"

    async def run(self, **kwargs):
        return CapabilityResult(text="ok")


@pytest.fixture
def stubs():
    """注册替身后复原注册表，避免污染其它用例的 list_available()。"""
    _LimitedStub.ran = 0
    CapabilityRegistry.register(_LimitedStub)   # 幂等；上一轮 teardown 摘掉过
    CapabilityRegistry.register(_UnlimitedStub)
    yield _LimitedStub
    for key in [k for k, v in CapabilityRegistry._capabilities.items()
                if v in (_LimitedStub, _UnlimitedStub)]:
        CapabilityRegistry._capabilities.pop(key, None)


def test_quota_counts_per_request_not_per_instance(stubs):
    """关键回归：换一个实例不能让计数归零——老实现就是这么失效的。"""
    ctx = {}
    assert usage.claim(ctx, "quota_stub") is True
    assert CapabilityRegistry.get("quota_stub") is not CapabilityRegistry.get("quota_stub")
    assert usage.claim(ctx, "quota_stub") is True
    assert usage.claim(ctx, "quota_stub") is False, "第 3 次必须被拦"
    assert usage.used(ctx, "quota_stub") == 2
    assert usage.remaining(ctx, "quota_stub") == 0


def test_unlimited_capability_never_blocked(stubs):
    ctx = {}
    assert [usage.claim(ctx, "quota_stub_unlimited") for _ in range(5)] == [True] * 5
    assert usage.remaining(ctx, "quota_stub_unlimited") is None
    assert usage.notes(ctx) == ""


def test_guard_drops_steps_beyond_limit(stubs):
    """计划里排了 4 次 → 只留 2 次，且必须留下明确的「已拦下」说明。"""
    sup = Supervisor(llm_client=None)
    decision = SupervisorDecision(
        complexity="complex", reasoning="",
        plan=[{"capability": "quota_stub", "goal": f"g{i}"} for i in range(4)],
    )
    out = sup._guard_decision(decision, ["quota_stub"], max_sub_tasks=8)
    assert [s["goal"] for s in out.plan] == ["g0", "g1"]
    assert "quota_stub" in usage.notes(sup._runtime_ctx)


def test_exhausted_caps_dropped_from_available_set(stubs):
    """已用满的能力要从可选集里物理摘掉，而不是继续摆在那里等 LLM 去调。"""
    ctx = {}
    usage.claim(ctx, "quota_stub")
    usage.claim(ctx, "quota_stub")
    assert usage.drop_exhausted(ctx, ["quota_stub", "quota_stub_unlimited"]) == ["quota_stub_unlimited"]


def test_dispatch_refuses_when_exhausted(stubs):
    """已用满的能力：哪怕还在计划里，也不执行。"""
    sup = Supervisor(llm_client=None)
    sup._runtime_ctx = {}
    usage.claim(sup._runtime_ctx, "quota_stub")
    usage.claim(sup._runtime_ctx, "quota_stub")
    todo = TodoList()
    todo.add("分析日志", capability="quota_stub")

    results = asyncio.run(sup._dispatch_group(
        [(0, {"capability": "quota_stub", "goal": "g"})], todo, None))

    assert _LimitedStub.ran == 0, "超限就不该跑"
    assert results["quota_stub"]["ok"] is False
    assert "上限" in results["quota_stub"]["error"]
    assert "quota_stub" in usage.notes(sup._runtime_ctx)
    # 拦下 ≠ 失败：必须留独立标记，否则上层只能看到 ok=False，
    # 会把它渲染成「分析失败」（用户会以为分析崩了，其实是机制层主动收敛）。
    assert results["quota_stub"]["meta"]["blocked"] == "quota"


def test_dispatch_within_limit_runs_and_stays_quiet(stubs):
    """计划本来就只排 2 次（正好用满）→ 照常执行，且不该留「被拦下」的噪音。"""
    sup = Supervisor(llm_client=None)
    sup._runtime_ctx = {}
    todo = TodoList()
    todo.add("第 1 次", capability="quota_stub")
    todo.add("第 2 次", capability="quota_stub")

    results = asyncio.run(sup._dispatch_group(
        [(0, {"capability": "quota_stub", "goal": "a"}),
         (1, {"capability": "quota_stub", "goal": "b"})], todo, None))

    assert _LimitedStub.ran == 2
    assert results["quota_stub"]["ok"] is True
    assert usage.notes(sup._runtime_ctx) == ""


def test_synthesize_carries_notes_into_final_text(stubs):
    """明确指令必须进汇总正文，否则整理答复的模型会以为还有更深的分析。"""
    results = {"quota_stub": {"ok": True, "text": "日志结论"}}
    note = usage.block({}, "quota_stub", "超出上限的派发已拦下")
    text = Supervisor._synthesize(results, note)
    assert "日志结论" in text and "上限" in text
    assert text.index("日志结论") < text.index("上限"), "结果在前、说明在后"
    assert Supervisor._synthesize(results) == "[quota_stub] ✅ 日志结论", "没有说明时保持原样"
