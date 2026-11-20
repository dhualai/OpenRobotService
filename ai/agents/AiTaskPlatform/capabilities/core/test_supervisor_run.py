"""Supervisor.run() 主流程：单轮基线与多轮追规。

为什么单独一个文件：`run()` 是每次 @U老师 的必经之路，但在本次改造前**零单测**——
`test_usage_quota.py` 全在绕开 LLM 直调 `_guard_decision`/`_dispatch_group`/`_synthesize`。
改核心流程却没有回归网，只能靠手动验，所以先把 run() 锁住。

不调真 LLM、不连库：假 LLM 按顺序吐 JSON 决策，替身能力直接返回文本。
"""

import asyncio
import json

import pytest

from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult
from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
from ai.agents.AiTaskPlatform.capabilities.core.supervisor import Supervisor


class _CapA(BaseCapability):
    name = "run_stub_a"
    description = "run() 测试替身 A"
    calls: list = []
    terminate = False

    async def run(self, **kwargs):
        type(self).calls.append(kwargs.get("query", ""))
        return CapabilityResult(text=f"A的结果({kwargs.get('query','')})",
                                terminate=type(self).terminate)


class _CapB(BaseCapability):
    name = "run_stub_b"
    description = "run() 测试替身 B"
    calls: list = []

    async def run(self, **kwargs):
        type(self).calls.append(kwargs.get("query", ""))
        return CapabilityResult(text="B的结果")


class _SeqLLM:
    """按顺序吐决策 JSON；记下每次收到的 prompt，用来断言次轮看到了「已执行」。"""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    async def complete(self, prompt, system_prompt=None, max_tokens=2000,
                       temperature=0.1, thinking=False):
        self.prompts.append(prompt)
        if self.decisions:
            return self.decisions.pop(0)
        # 决策用尽 → 兜底 simple（等价于「规划器说够了」）
        return _dec("simple")


def _dec(complexity="medium", plan=None, **kw):
    d = {"complexity": complexity, "reasoning": "r", "plan": plan or []}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def _step(cap, goal="查一下"):
    return {"capability": cap, "goal": goal, "parallel": True}


@pytest.fixture
def caps():
    _CapA.calls, _CapB.calls = [], []
    _CapA.terminate = False
    CapabilityRegistry.register(_CapA)
    CapabilityRegistry.register(_CapB)
    yield
    for key in [k for k, v in CapabilityRegistry._capabilities.items()
                if v in (_CapA, _CapB)]:
        CapabilityRegistry._capabilities.pop(key, None)


def _run(llm, ctx=None, caps=None):
    return asyncio.run(Supervisor(llm_client=llm).run(
        task_context="工单上下文",
        available_caps=caps or ["run_stub_a", "run_stub_b"],
        runtime_ctx=ctx if ctx is not None else {},
    ))


# ── 单轮基线（改造前就该绿，改造后必须仍然绿）────────────────

def test_simple_plans_once_and_dispatches_nothing(caps):
    """规划器说 simple → 只规划一次、一个能力都不派。"""
    llm = _SeqLLM(_dec("simple"))
    res = _run(llm)

    assert len(llm.prompts) == 1
    assert _CapA.calls == [] and _CapB.calls == []
    assert res["results"] == {}
    assert res["plan"] == []


def test_planned_capability_runs_and_result_is_returned(caps):
    """派了的能力真的跑、结果按能力名回到 results。"""
    llm = _SeqLLM(_dec("medium", [_step("run_stub_a", "查A")]))
    res = _run(llm)

    assert _CapA.calls == ["查A"]
    assert res["results"]["run_stub_a"]["text"] == "A的结果(查A)"
    assert res["results"]["run_stub_a"]["ok"] is True


def test_results_keyed_by_capability_name(caps):
    """results 以**能力名**为 key —— 这是多轮里「同一能力只能出现一次」的根因。"""
    llm = _SeqLLM(_dec("complex", [_step("run_stub_a", "A"), _step("run_stub_b", "B")]))
    res = _run(llm)

    assert set(res["results"].keys()) == {"run_stub_a", "run_stub_b"}


# ── 多轮追规 ────────────────────────────────────────────────

def test_second_round_planner_sees_what_already_ran(caps):
    """追规那轮必须看得到「上一轮派了什么、结果如何」，否则只会重复派。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "查A")]),
        _dec("simple"),                      # 次轮：够了
    )
    _run(llm)

    assert len(llm.prompts) == 2, "应该规划两次（初始 + 追规）"
    second = llm.prompts[1]
    assert "run_stub_a" in second, "次轮 prompt 必须提到已派过的能力"
    assert "A的结果(查A)" in second, "次轮 prompt 必须带上已拿到的结果"


def test_second_round_cannot_redispatch_a_done_capability(caps):
    """次轮再要派同一能力 → 直接不排（results 以能力名为 key，覆盖等于吞掉证据）。"""
    _CapB.calls.clear()
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "第一次")]),
        _dec("complex", [_step("run_stub_a", "又来一次")]),   # 非法：A 已派过
    )
    res = _run(llm)

    assert _CapA.calls == ["第一次"], "A 不应被派第二次"
    assert res["results"]["run_stub_a"]["text"] == "A的结果(第一次)", "第一轮证据不能被覆盖"


def test_terminate_stops_before_the_next_round(caps):
    """历史方案已验证（terminate）→ 不该再白跑一轮昂贵能力。"""
    _CapA.terminate = True
    llm = _SeqLLM(_dec("medium", [_step("run_stub_a", "查历史")]))
    _run(llm)

    assert len(llm.prompts) == 1, "terminate 之后不该再规划"


def test_at_most_two_rounds(caps):
    """上限就是 2：两轮都想派也只规划两次。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("medium", [_step("run_stub_b", "二")]),
        _dec("medium", [_step("run_stub_b", "三")]),   # 不该被用到
    )
    _run(llm)

    assert len(llm.prompts) == 2
    assert _CapB.calls == ["二"]


def test_quota_block_is_marked_explicitly(caps):
    """配额拦下必须留**独立标记** —— 否则上层只能看到 ok=False，会渲染成「分析失败」。

    用 `_CapA` 预先把台账占满（A 本身无上限，这里直接写台账模拟「上一轮已用满」）。
    """
    from ai.agents.AiTaskPlatform.capabilities.core import usage

    ctx = {}
    usage.ledger(ctx)
    ctx["cap_usage"]["run_stub_b"] = 0     # 确保台账存在（无上限能力不会被拦）

    res = _run(_SeqLLM(_dec("medium", [_step("run_stub_b", "B")])), ctx=ctx)
    assert res["results"]["run_stub_b"]["ok"] is True



# ── 返回形状与埋点 ──────────────────────────────────────────

def test_two_rounds_append_plan_and_capability_spans_in_order(caps):
    """过程区/span 树靠结构渲染：两轮应追加第二组「plan + 能力」，顺序不乱。

    （`tracing/test_trace_bus.py` 里那条 `names == ["plan", ...]` 是**手工构造 span**
      的纯 TraceBus 测试，不走 run()，锁不住这块。）
    """
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("medium", [_step("run_stub_b", "二")]),
    )
    res = _run(llm)

    roots = [s for s in res["spans"] if s["name"] == "supervisor.run"]
    assert len(roots) == 1, "整场仍归一个根节点"
    names = [c["name"] for c in roots[0]["children"]]
    assert names == ["plan", "run_stub_a", "plan", "run_stub_b"], \
        f"两轮应各留一组 plan + 能力，实际 {names}"
    assert names.count("plan") == 2


def test_rounds_are_reported_for_trace(caps):
    """每轮留下一条可读记录（派了什么 / 为什么停），供 trace 与排查。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("simple"),
    )
    res = _run(llm)

    assert [r["round"] for r in res["rounds"]] == [1, 2]
    assert res["rounds"][0]["planned"] == ["run_stub_a"]
    assert "stop" in res["rounds"][1], "停的那轮要写清原因"


def test_plan_is_cumulative_and_complexity_from_first_round(caps):
    """plan 累计两轮（上层只用于展示）；complexity 取**首轮**。"""
    llm = _SeqLLM(
        _dec("complex", [_step("run_stub_a", "一")]),
        # 次轮仍要派 → 不能写 simple（simple 会带空 plan 并直接结束这一轮）
        _dec("medium", [_step("run_stub_b", "二")]),
    )
    res = _run(llm)

    assert [s["capability"] for s in res["plan"]] == ["run_stub_a", "run_stub_b"]
    assert res["complexity"] == "complex", "不该被次轮的 simple 覆盖"


def test_note_key_is_separate_from_results(caps):
    """配额说明走独立的 note 键，不污染 results（上层按能力名读 results）。"""
    llm = _SeqLLM(_dec("simple"))
    res = _run(llm)

    assert res["note"] == "", "没有拦下时不该有说明"
    assert res["final_text"] == "", "没派任何能力时没有汇总正文"


def test_executed_block_stays_bounded(caps):
    """喂回规划器的「已执行」段要截断 —— 否则等于把长结果再灌一遍。"""
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import (
        _EXECUTED_BLOCK_CHARS,
        _executed_block,
    )

    long_text = "很长的结论" * 2000
    block = _executed_block([{"capability": "x", "goal": "g"}], {"x": {"ok": True, "text": long_text}})
    assert len(block) <= _EXECUTED_BLOCK_CHARS
    assert "已截断" in block


# ── B：能力结果互相喂养 ─────────────────────────────────────

def test_default_wave_order_is_unchanged(caps):
    """没有任何步骤写 after 时，必须沿用改造前的顺序：检索先、日志后。

    （这条默认值靠 _plan_waves 用同一套机制表达，不是另一条代码路径。）
    """
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import _plan_waves

    plan = [{"capability": "log_analyze"}, {"capability": "retrieve_kb"}]
    waves = _plan_waves(plan)
    caps_of = lambda w: [s["capability"] for _, s in w]        # noqa: E731
    assert len(waves) == 2, "旧行为就是两波"
    assert caps_of(waves[0]) == ["retrieve_kb"], "非日志的先跑"
    assert caps_of(waves[1]) == ["log_analyze"], "日志后跑（历史命中就不用读了）"


def test_no_log_analyze_is_one_wave(caps):
    """没有 log_analyze 时不该凭空多出一波。"""
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import _plan_waves

    waves = _plan_waves([{"capability": "run_stub_a"}, {"capability": "run_stub_b"}])
    assert len(waves) == 1
    assert sorted(s["capability"] for _, s in waves[0]) == ["run_stub_a", "run_stub_b"]


def test_explicit_after_orders_and_injects(caps):
    """显式 after：后跑的那步要等到前一步的**结果**，并拿到它作为输入。"""
    llm = _SeqLLM(
        _dec("complex", [
            _step("run_stub_a", "先查A"),
            {"capability": "run_stub_b", "goal": "据A继续", "parallel": True,
             "after": ["run_stub_a"]},
        ]),
    )
    _run(llm)

    assert _CapB.calls, "B 应该跑"
    assert "先查A" in _CapB.calls[0], "B 的 query 要带上自己的 goal"
    assert "A的结果(先查A)" in _CapB.calls[0], "B 必须拿到 A 的结果（这就是互相喂养）"


def test_after_without_dependency_gets_no_injection(caps):
    """没写 after 的步骤不该被灌上游结论 —— 否则等于给 query 掺水。"""
    llm = _SeqLLM(_dec("complex", [
        _step("run_stub_a", "查A"),
        _step("run_stub_b", "查B"),
    ]))
    _run(llm)

    assert _CapB.calls == ["查B"], "B 的 query 应当原样，不含 A 的结果"
    assert _CapA.calls == ["查A"]


def test_after_pointing_outside_plan_is_dropped(caps):
    """after 指向没排进 plan 的能力 → 摘掉，否则那一波永远等不到（或变成并跑）。"""
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import (
        Supervisor,
        SupervisorDecision,
    )

    sup = Supervisor(llm_client=None)
    sup._runtime_ctx = {}
    d = SupervisorDecision(complexity="complex", plan=[
        {"capability": "run_stub_a", "goal": "g", "after": ["根本没这能力"]},
    ])
    out = sup._guard_decision(d, ["run_stub_a", "run_stub_b"], 5)
    assert "after" not in out.plan[0], "落空的依赖必须摘掉"


def test_cyclic_after_does_not_deadlock(caps):
    """after 成环 → 剩下的并作一波照跑，绝不卡死。"""
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import _plan_waves

    waves = _plan_waves([
        {"capability": "run_stub_a", "after": ["run_stub_b"]},
        {"capability": "run_stub_b", "after": ["run_stub_a"]},
    ])
    assert len(waves) == 1, "成环时并跑，不是无限等"
    assert len(waves[0]) == 2


def test_upstream_block_is_bounded(caps):
    """注入的上游结论要截断。"""
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import (
        _EXECUTED_BLOCK_CHARS,
        _upstream_block,
    )

    step = {"capability": "b", "after": ["a"]}
    prev = {"a": {"ok": True, "text": "结论" * 5000}}
    block = _upstream_block(step, prev)
    assert block and len(block) <= _EXECUTED_BLOCK_CHARS
    assert "已截断" in block


# ── C：失败的能力可以在追规那轮重试 ────────────────────────

class _FlakyCap(BaseCapability):
    """第一次失败、第二次成功 —— 模拟「一次抖动毁掉整轮」。"""
    name = "run_stub_flaky"
    description = "第一次失败、之后成功的替身"
    attempts: list = []

    async def run(self, **kwargs):
        type(self).attempts.append(kwargs.get("query", ""))
        if len(type(self).attempts) == 1:
            return CapabilityResult.failure("第一次失败（模拟抖动）")
        return CapabilityResult(text=f"第二次成功({kwargs.get('query','')})")


@pytest.fixture
def flaky():
    _FlakyCap.attempts = []
    CapabilityRegistry.register(_FlakyCap)
    yield
    for key in [k for k, v in CapabilityRegistry._capabilities.items() if v is _FlakyCap]:
        CapabilityRegistry._capabilities.pop(key, None)


def test_failed_capability_can_be_retried_in_the_next_round(flaky):
    """失败的能力不该被和「成功的能力」一样禁掉：它没有正文可覆盖，重试是纯赚。

    （这正是「一次 LLM 抖动毁掉整轮」的直接修法 —— 追规那轮给了它第二次机会。）
    """
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_flaky", "第一次问法")]),
        _dec("medium", [_step("run_stub_flaky", "换个说法再问")]),
    )
    res = _run(llm, caps=["run_stub_flaky"])

    assert len(_FlakyCap.attempts) == 2, "失败的能力应该被再派一次"
    assert "换个说法再问" in _FlakyCap.attempts[1]
    assert res["results"]["run_stub_flaky"]["ok"] is True
    assert "第二次成功" in res["results"]["run_stub_flaky"]["text"]


def test_only_failed_ones_are_retryable(caps, flaky):
    """混在一起验：成功的不重派、失败的可以 —— 别把 C 做过头。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "查A"), _step("run_stub_flaky", "查flaky")]),
        _dec("medium", [_step("run_stub_a", "又查一次A"), _step("run_stub_flaky", "换个说法查flaky")]),
    )
    _run(llm, caps=["run_stub_a", "run_stub_flaky"])

    assert _CapA.calls == ["查A"], "成功的能力不能被重派（证据会被覆盖）"
    assert len(_FlakyCap.attempts) == 2, "失败的能力应该被重派"
    assert "换个说法查flaky" in _FlakyCap.attempts[1]


def test_executed_block_marks_retryable(flaky):
    """「本轮已执行」段要标清哪条能再试、哪条不能，否则规划器只会瞎猜。"""
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import _executed_block

    block = _executed_block(
        [{"capability": "a", "goal": "g"}, {"capability": "b", "goal": "g"}],
        {"a": {"ok": True, "text": "有结论"}, "b": {"ok": False, "error": "抖动"}},
    )
    assert "已成功，不要重复派" in block
    assert "可换个说法再派一次" in block


# ── D：原生工具调用取计划（拿不到就回退 JSON）────────────────

class _ToolLLM:
    """只会原生工具调用的假 LLM：按需返回 submit_plan 的 tool_calls。"""

    def __init__(self, arguments=None, *, name="submit_plan", raise_exc=None):
        self.arguments = arguments
        self.name = name
        self.raise_exc = raise_exc
        self.calls = 0
        self.used_complete = 0

    async def complete_with_tools(self, tools, messages=None, prompt=None,
                                  system_prompt=None, max_tokens=2000,
                                  temperature=0.1, thinking=None):
        self.calls += 1
        assert tools and tools[0]["function"]["name"] == "submit_plan"
        if self.raise_exc:
            raise self.raise_exc
        return {"content": "", "reasoning": "", "tool_calls": [
            {"id": "call_1", "name": self.name, "arguments": self.arguments}
        ]}

    async def complete(self, prompt, system_prompt=None, max_tokens=2000,
                       temperature=0.1, thinking=False):
        self.used_complete += 1
        return _dec("simple")           # JSON 回退路径


def test_tool_path_is_used_when_available(caps):
    """llm 支持工具调用时，用它取计划 —— 参数已是 dict，不必再抠 JSON。"""
    llm = _ToolLLM({"complexity": "medium",
                    "plan": [{"capability": "run_stub_a", "goal": "查A"}]})
    res = _run(llm)

    # 每轮各调一次（两轮 = 2 次）；关键是**一次都没走 JSON 路径**
    assert llm.calls == 2
    assert llm.used_complete == 0, "走了工具路径就不该再走 JSON 路径"
    assert _CapA.calls == ["查A"]
    assert res["complexity"] == "medium"


def test_tool_path_not_called_when_llm_lacks_it(caps):
    """llm 没有 complete_with_tools（多数测试替身如此）→ 直接走 JSON 路径，行为不变。"""
    llm = _SeqLLM(_dec("medium", [_step("run_stub_a", "查A")]))
    res = _run(llm)
    assert _CapA.calls == ["查A"]
    assert res["results"]["run_stub_a"]["ok"] is True


def test_falls_back_to_json_when_tool_call_errors(caps):
    """工具调用抛错 → 回退 JSON 路径，绝不让调度整个失败。"""
    llm = _ToolLLM(None, raise_exc=RuntimeError("中转站不认 tools"))
    res = _run(llm)

    assert llm.used_complete >= 1, "必须回退到 JSON 路径"
    assert res["complexity"] in ("simple", "medium", "complex")


def test_falls_back_when_model_calls_a_different_tool(caps):
    """模型没调 submit_plan（调了别的/没调）→ 也要回退，而不是当成空计划。"""
    llm = _ToolLLM({"plan": []}, name="some_other_tool")
    res = _run(llm)
    assert llm.used_complete >= 1


def test_tool_arguments_non_dict_falls_back(caps):
    """arguments 不是 dict（中转站给字符串）→ 回退。"""
    llm = _ToolLLM("这是个字符串不是 dict")
    res = _run(llm)
    assert llm.used_complete >= 1


def test_tool_plan_goes_through_the_same_guard(caps):
    """工具路径的计划同样要过护栏：未知能力照样被丢弃。"""
    llm = _ToolLLM({"complexity": "complex",
                    "plan": [{"capability": "根本没这能力", "goal": "x"},
                             {"capability": "run_stub_a", "goal": "查A"}]})
    res = _run(llm)
    assert _CapA.calls == ["查A"]
    assert "根本没这能力" not in res["results"]


def test_tool_path_supports_after(caps):
    """工具路径也要支持 after（否则 D 会把 B 的能力掐掉一半）。"""
    llm = _ToolLLM({"complexity": "complex", "plan": [
        {"capability": "run_stub_a", "goal": "先查A"},
        {"capability": "run_stub_b", "goal": "据A继续", "after": ["run_stub_a"]},
    ]})
    _run(llm)
    assert "A的结果(先查A)" in _CapB.calls[0]


# ── F：墙上时钟安全阀 + 耗时观测 ─────────────────────────────

def test_deadline_stops_starting_new_rounds(caps):
    """超时不打断正在跑的能力，但**不再开始新的一轮**。

    注意它只在「已经有结果」时才挡 —— 第一轮还没跑就超时说明是环境异常，
    那种情况交给上层的超时/异常处理，不该在这里静默返回空。
    """
    import ai.agents.AiTaskPlatform.capabilities.core.supervisor as supmod

    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("medium", [_step("run_stub_b", "二")]),   # 不该被用到
    )
    orig = supmod._max_run_seconds
    supmod._max_run_seconds = lambda: -1.0             # 立刻过期
    try:
        res = _run(llm)
    finally:
        supmod._max_run_seconds = orig

    assert _CapA.calls == ["一"], "第一轮照跑（不因为阀值就什么都不做）"
    assert _CapB.calls == [], "第二轮不该开始"
    assert any(r.get("stop") == "超时收束" for r in res["rounds"])
    assert len(llm.prompts) == 1, "也不该再调一次规划 LLM"


def test_deadline_does_not_fire_normally(caps):
    """默认上限很宽松：正常两轮不该被它影响。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("medium", [_step("run_stub_b", "二")]),
    )
    res = _run(llm)

    assert len(llm.prompts) == 2
    assert _CapB.calls == ["二"]
    assert not any(r.get("stop") == "超时收束" for r in res["rounds"])


def test_elapsed_is_reported(caps):
    """耗时/轮次要留在结果里（定预算的依据是分布，不是拍脑袋）。"""
    llm = _SeqLLM(_dec("simple"))
    res = _run(llm)
    assert isinstance(res["elapsed_ms"], int)
    assert res["elapsed_ms"] >= 0
    assert res["rounds"], "轮次摘要要在"
