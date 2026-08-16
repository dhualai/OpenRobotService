"""Supervisor — 自主派生子 Agent 的通用编排内核（产品无关）

对标 Claude **Orchestrator-workers**：
  - 中央调度 Agent（Supervisor）评估任务复杂度 → 出 plan → 转 TodoList → 执行循环
  - 按需派生子 Agent / 能力（CapabilityRegistry），动态决定派几个、何时收束

设计约定（见 TASK_AGENT_TARGET_ARCH.md §6c）：
  - **F3 = A**：调度决策用 LLM 自主（评估复杂度 + 出 plan）
  - **F7**：TodoList 自我任务管理（本版加入，平铺）
  - **产品无关内核**：Supervisor 不懂工单/产品知识，只懂"如何调度能力、如何分配、何时收束"
  - **服务端确定性兜底优先**：LLM 调度只做"建议"，程序用硬护栏强校验（见 _guard_decision）
  - **F6**：本版物理放 AiTaskPlatform/capabilities/，但代码写得通用（可移动复用）

Capability / 调度的抽象：
  - 调度 LLM 的输入：任务上下文 + 可用能力清单（list_available）+ 附件概况 + 讨论历史
  - 调度 LLM 的输出：{"complexity", "reasoning", "plan":[{capability,goal,parallel}], "ask_user"}
  - 程序护栏：plan 的 capability 必须在 list_available 内；complexity=simple 强制 0 派生；
    max_rounds 取 min(llm, 程序上限)；并发用 asyncio.Semaphore 控制

用法（由具体 Flow 接入，如 discuss_flow）：
    supervisor = Supervisor(llm_client=..., plan_prompt_builder=...)
    result = await supervisor.run(task_context="...", available_caps=CapabilityRegistry.list_available())
"""

from __future__ import annotations

import asyncio
import json
import re
import time as _time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
from ai.agents.AiTaskPlatform.capabilities.core.supervisor_todo import TodoList, TodoItem

logger = get_logger("TASK_AGENT")

# ── 程序强制护栏（服务端优先，不信任 LLM）──
MAX_SUB_TASKS = 5          # 一次最多派生子任务数（防无限分叉）；与 _CONCURRENCY 配合：可派 5 个，但最多同时跑 _CONCURRENCY 个
MAX_ROUNDS_PER_TASK = 3    # 每个子任务内部编排轮数上限（由子能力内部执行，如 LogSubAgent/日志分析的轮数；此处为对齐锚点）
SOFT_LIMIT = 2             # 调度 LLM 输出解析失败时的重试次数上限（浅则重试，达到上限走兜底）
_CONCURRENCY = 3           # 同时最多并行执行几个子任务（asyncio.Semaphore，防拉爆 API/限流）


@dataclass
class SupervisorDecision:
    """Supervisor 调度决策（程序解析 LLM 输出后的结构化结果）。"""
    complexity: str = "simple"           # simple | medium | complex
    reasoning: str = ""
    plan: list[dict] = field(default_factory=list)  # [{capability, goal, parallel}]
    ask_user: bool = False

    def is_simple(self) -> bool:
        return self.complexity == "simple"


class SupervisorError(Exception):
    """Supervisor 调度或执行错误（外层可捕获降级）。"""


# LLM 调度决策输出解析（健壮 JSON 解析）
def _parse_decision(raw: str) -> Optional[SupervisorDecision]:
    """从调度 LLM 原始输出解析 SupervisorDecision。

    多层容错（应对 DeepSeek 常见的不规范 JSON）：
      1) 剥 Markdown 代码块（```json ... ``` 或 ``` ... ```）；
      2) 按花括号配对截取最外层 {...} 对象（含嵌套对象，避免非贪婪提前截断）；
      3) json.loads → 清理尾随逗号后再试 → ast.literal_eval（容忍单引号）→
         宽松 JSON5（去注释/float 幂）；
      4) 整体失败时按字段正则逐个捞 (complexity/plan/ask_user)。
    """
    text = (raw or "").strip()
    if not text:
        return None

    # 1. 剥 ```json ... ``` / ``` ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    # 2. 按花括号配对截取最外层对象（容忍 JSON 内嵌套，靠配平 {} 而非正则非贪婪）
    s = text.find("{")
    if s != -1:
        depth = 0
        for i in range(s, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    text = text[s : i + 1]
                    break

    # 3. 多级 JSON 解析尝试
    data = _loads_lax(text)
    if data is None:
        # 4. 字段级兜底：整体 JSON 解析无望时，逐个捞字段
        return _decision_from_fields(text)
    if not isinstance(data, dict):
        return None

    plan = data.get("plan") or []
    if not isinstance(plan, list):
        plan = []
    clean_plan = []
    for p in plan:
        if isinstance(p, dict) and p.get("capability"):
            item = {
                "capability": str(p["capability"]),
                "goal": str(p.get("goal", "")),
                "parallel": bool(p.get("parallel", False)),
            }
            for extra_key in ("window_minutes", "occurred_at", "params"):
                if extra_key in p and p[extra_key] is not None:
                    item[extra_key] = p[extra_key]
            clean_plan.append(item)
    return SupervisorDecision(
        complexity=str(data.get("complexity", "simple")),
        reasoning=str(data.get("reasoning", "")),
        plan=clean_plan,
        ask_user=bool(data.get("ask_user", False)),
    )


def _loads_lax(text: str):
    """多级 JSON 解析：标准 → 清尾随逗号 → literal_eval(单引号) → JSON5 宽松。"""
    if not text:
        return None
    # 1) 标准
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2) 尾随逗号清理（{...} 与 [...] 的 ,} / ,] ）
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # 3) true/false/null JSON 常量转 Python；再用 literal_eval 容忍单引号
    py_like = cleaned \
        .replace("true", "True").replace("false", "False").replace("null", "None")
    try:
        import ast
        return ast.literal_eval(py_like)
    except Exception:
        pass
    # 4) 剥 // 与 /* */ 注释后，再用上述栈再试一次
    no_comment = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL))
    try:
        return json.loads(no_comment)
    except Exception:
        return None


def _decision_from_fields(text: str) -> Optional[SupervisorDecision]:
    """整体 JSON 解析失败时的字段级兜底：逐个用正则捞 complexity/plan/ask_user。"""
    import re as _re
    data: dict = {}

    # complexity: 优先取 simple|medium|complex
    cm = _re.search(r'"?complexity"?\s*[:=]\s*"?(simple|medium|complex)"?', text, _re.IGNORECASE)
    if cm:
        data["complexity"] = cm.group(1).lower()

    # ask_user
    am = _re.search(r'"?ask_user"?\s*[:=]\s*"?(true|false|1|0)"?', text, _re.IGNORECASE)
    if am:
        data["ask_user"] = am.group(1).lower() in ("true", "1")

    # plan：一组 (capability, goal) 尽量捞出来
    plan = []
    # 匹配 "capability": "xxx" 邻近的 goal（同一 plan 项）
    for pm in _re.finditer(r'"capability"\s*:\s*"([^"]+)"', text):
        cap = pm.group(1).strip()
        if not cap:
            continue
        # 该项的 goal：capability 之后同段内尽量近的 "goal":"..."
        tail = text[pm.end() : pm.end() + 400]
        gm = _re.search(r'"goal"\s*:\s*"([^"]*)"', tail)
        item = {"capability": cap, "goal": gm.group(1) if gm else "", "parallel": False}
        plan.append(item)

    if plan:
        data["plan"] = plan
    else:
        data["plan"] = []

    if not data.get("complexity") and not plan:
        return None
    return SupervisorDecision(
        complexity=str(data.get("complexity", "simple")),
        reasoning="",
        plan=data.get("plan", []),
        ask_user=bool(data.get("ask_user", False)),
    )


class Supervisor:
    """通用编排内核。构造时注入 llm_client 与 plan prompt 构建回调。

    设计为产品无关：`task_context` 是任意字符串（工单描述 / 平台报错 / 系统日志...），
    由调用方（Flow）构造；Supervisor 不解析其领域语义，只把它和可用能力清单交给调度 LLM。
    """

    def __init__(
        self,
        llm_client: Any,
        plan_prompt_builder: Optional[Callable[[str, list[str]], str]] = None,
    ):
        """
        Args:
            llm_client: 兼容 `.complete(prompt, system_prompt=..., max_tokens=..., temperature=...)`
            plan_prompt_builder: 可选；给定 (task_context, available_caps) 返回调度 prompt。
                为 None 时用内置默认 prompt。
        """
        self._llm = llm_client
        self._build_plan_prompt = plan_prompt_builder or self._default_plan_prompt
        self._runtime_ctx: dict = {}  # 由 run() 设置；派能力时注入 kwargs

    # ── 默认调度 prompt（产品无关，仅描述"如何规划排查"）──
    @staticmethod
    def _default_plan_prompt(task_context: str, cap_names: list[str]) -> str:
        caps = "\n".join(f"- {n}: {_cap_desc(n)}" for n in cap_names) or "（无可用能力）"
        return (
            "你是排查任务的调度器。根据任务上下文和可用能力，决定是否派生子任务、派哪些。\n\n"
            f"## 可用能力\n{caps}\n\n"
            "## 任务上下文\n"
            f"{task_context}\n\n"
            "## 输出（仅 JSON，无其他文字）\n"
            '{"complexity":"simple|medium|complex","reasoning":"...",'
            '"plan":[{"capability":"<能力名，必须来自可用能力清单>","goal":"...","parallel":bool,"window_minutes":<可选，仅log_analyze>}],'
            '"ask_user":bool}\n'
            "规则：\n"
            "- 任务简单（纯知识问答、无多领域线索）→ complexity=simple，plan 为空\n"
            "- 单一领域线索 → complexity=medium，plan 含 1 项\n"
            "- 多领域线索交叉 → complexity=complex，plan 含多项，parallel 合理设 true\n"
            "- capability 只能从可用能力清单里选，严禁凭空命名\n"
            "- 若派 log_analyze 且故障属缓慢累积/日志稀疏，可在 window_minutes 指定更长的前因窗口（默认15即可）\n"
        )

    # ── 主入口 ──
    async def run(
        self,
        task_context: str,
        available_caps: Optional[list[str]] = None,
        max_sub_tasks: int = MAX_SUB_TASKS,
        runtime_ctx: Optional[dict] = None,
        on_progress: Optional[Callable[[dict], Any]] = None,
    ) -> dict:
        """执行一次排查编排，返回结构化结果。

        Args:
            task_context: 任务上下文文本（给调度 LLM 用，产品无关）
            available_caps: 可用能力名清单（默认取 list_available）
            max_sub_tasks: 最多派生数
            runtime_ctx: 运行时上下文 dict（产品无关），派给每个能力时注入 kwargs。
                能力所需的资源（如 log_analyze 的 log_path、robot_type 等）从这里拿，
                调度 LLM 不需要知道这些。
            on_progress: 可选进度回调，每当某个能力状态变更（开始/完成）时触发一次，
                入参为 {"id","description","status","capability","phase"}，供上层把
                todo 的实时进展推给前端（类似 Claude Code 的动态执行过程）。

        Returns:
            {
              "complexity", "plan", "ask_user",
              "todo": [TodoItem dict...],
              "results": {capability_name: CapabilityResult dict...},
              "final_text": 汇总文本，
              "trace": 调度记录，
            }
        """
        t0 = _time.perf_counter()
        caps = available_caps or CapabilityRegistry.list_available()
        self._runtime_ctx = runtime_ctx or {}  # 供 _dispatch 注入给能力
        self._on_progress = on_progress  # 实时进度回调（逐项能力推送，供前端动态展示）

        def _emit(phase: str, td: dict) -> None:
            if self._on_progress is None:
                return
            try:
                self._on_progress({**td, "phase": phase, "capability": td.get("capability", "")})
            except Exception:
                pass

        # 1. 调度决策（LLM 建议）
        decision = await self._plan(task_context, caps, max_sub_tasks)

        # 2. 建 todo（F7：plan 转 TodoList）
        todo = TodoList()
        for step in decision.plan:
            todo.add(step.get("goal") or step.get("capability"), capability=step.get("capability", ""))

        # 3. simple → 0 派生，直接返回（无 plan）
        if decision.is_simple() or not decision.plan:
            return {
                "complexity": decision.complexity,
                "plan": decision.plan,
                "ask_user": decision.ask_user,
                "todo": todo.to_dict_list(),
                "results": {},
                "final_text": "",
                "elapsed_ms": round((_time.perf_counter() - t0) * 1000),
                "_decision": decision.__dict__,
            }

        # 4. 执行 plan（派生子任务）
        results = await self._dispatch(decision.plan, todo, max_sub_tasks, _emit)

        # 5. 汇总
        return {
            "complexity": decision.complexity,
            "plan": decision.plan,
            "ask_user": decision.ask_user,
            "todo": todo.to_dict_list(),
            "results": results,
            "final_text": self._synthesize(results),
            "elapsed_ms": round((_time.perf_counter() - t0) * 1000),
            "_decision": decision.__dict__,
        }

    # ── 调度决策 ──
    async def _plan(self, task_context: str, caps: list[str], max_sub_tasks: int) -> SupervisorDecision:
        """调用调度 LLM，评估复杂度 + 出 plan。

        调度 LLM 输出解析失败时：浅轮数（< SOFT_LIMIT）重试，达到上限则按 simple 兜底
        （延续"服务端确定性兜底优先"）。
        """
        prompt = self._build_plan_prompt(task_context, caps)
        decision = None
        for attempt in range(SOFT_LIMIT + 1):
            raw = await self._llm.complete(
                prompt=prompt,
                system_prompt="你是一个严谨的排查任务调度器，只输出 JSON。",
                max_tokens=300,
                temperature=0.0,
            )
            decision = _parse_decision(raw)
            if decision is not None:
                break
            logger.warning(
                f"[supervisor] 调度输出解析失败 (attempt {attempt + 1}/{SOFT_LIMIT + 1}); "
                f"raw={repr((raw or '')[:300])}"
            )

        if decision is None:
            logger.warning("[supervisor] 调度解析多次失败，按 simple 兜底")
            return SupervisorDecision(complexity="simple", reasoning="调度解析多次失败，兜底为 simple")

        # 程序强制护栏（服务端优先，不信任 LLM）
        return self._guard_decision(decision, caps, max_sub_tasks)

    def _guard_decision(self, d: SupervisorDecision, caps: list[str], max_sub_tasks: int) -> SupervisorDecision:
        """强护栏：
          - simple 强制 0 派生
          - plan 里的 capability 必须在可用清单内（否者丢弃 + 记 trace）
          - 派生数不能超过 max_sub_tasks
        """
        valid_caps = set(caps)
        if d.is_simple():
            d.plan = []  # 强制 0 派生，防过度设计
            return d
        kept = []
        for step in d.plan:
            if step["capability"] in valid_caps:
                kept.append(step)
            else:
                logger.warning(f"[supervisor] 丢弃未知能力: {step['capability']}")
        # 数量上限
        d.plan = kept[:max_sub_tasks]
        return d

    # ── 派生子任务（支持并行/串行 + 并发上限）──
    async def _dispatch(self, plan: list[dict], todo: TodoList, max_sub_tasks: int, emit: Optional[Callable] = None) -> dict:
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _run_one(step: dict, todo_item: TodoItem):
            cap_name = step["capability"]
            cap = CapabilityRegistry.get(cap_name)
            if cap is None or not cap.is_available():
                todo.mark_done(todo_item.id, result=f"能力不可用: {cap_name}")
                return cap_name, {"ok": False, "error": f"能力不可用: {cap_name}", "text": ""}
            todo.mark_in_progress(todo_item.id)
            if emit is not None:
                emit("running", {"id": todo_item.id, "description": todo_item.description, "status": "in_progress", "capability": cap_name})
            try:
                async with sem:
                    # 把调度 LLM 定的 goal 与运行时上下文(runtime_ctx)一起传给能力
                    # query=goal（语义）：goal 即"这个子任务要解决什么"，能力以 query 接收
                    kwargs = {"query": step.get("goal", "")}
                    kwargs.update(self._runtime_ctx)  # 注入 log_path / robot_type 等
                    # 透传 plan 里的能力专属参数（如 window_minutes / occurred_at）
                    for extra_key in ("window_minutes", "occurred_at", "params"):
                        if extra_key in step and step[extra_key] is not None:
                            kwargs[extra_key] = step[extra_key]
                    result = await cap(**kwargs)      # 统一入口 __call__（含配额/异常兜底）
            except Exception as e:  # 极外层保险
                result = {"ok": False, "error": f"{type(e).__name__}: {e}", "text": ""}
            # 归一化为 dict（cap 返回 CapabilityResult 或 dict）
            res_dict = result.to_dict() if hasattr(result, "to_dict") else (result if isinstance(result, dict) else {"text": str(result), "ok": True})
            todo.mark_done(todo_item.id, result=str(res_dict.get("text", ""))[:80])
            if emit is not None:
                emit("done", {"id": todo_item.id, "description": todo_item.description, "status": "completed", "capability": cap_name})
            return cap_name, res_dict

        # 按 parallel 分组：并行组用 gather，串行组顺序执行
        if any(step.get("parallel") for step in plan):
            # 简单实现：所有任务都进 gather（由 Semaphore 控并发），保证不超限
            todo_items = todo._items[: len(plan)]
            tasks = [_run_one(step, todo_items[i]) for i, step in enumerate(plan)]
            done = await asyncio.gather(*tasks, return_exceptions=True)
            results = {}
            for i, (cap_name, res) in enumerate(done):
                if isinstance(res, BaseException):
                    results[plan[i]["capability"]] = {"ok": False, "error": str(res)}
                else:
                    results[cap_name] = res
            return results
        else:
            results = {}
            todo_items = todo._items[: len(plan)]
            for i, step in enumerate(plan):
                cap_name, res = await _run_one(step, todo_items[i])
                results[cap_name] = res
            return results

    # ── 汇总 ──
    @staticmethod
    def _synthesize(results: dict) -> str:
        """把各子任务结果拼成最终文本（简单拼接，供上层 LLM 继续整理）。"""
        parts = []
        for cap_name, res in results.items():
            if isinstance(res, dict):
                text = res.get("text", "")
                ok = res.get("ok", True)
                tag = "✅" if ok else "⚠️"
                if text:
                    parts.append(f"[{cap_name}] {tag} {text}")
                elif not ok:
                    parts.append(f"[{cap_name}] ⚠️ {res.get('error', '执行失败')}")
        return "\n\n".join(parts)


def _cap_desc(name: str) -> str:
    """获取能力描述（供调度 prompt 用）。"""
    cap = CapabilityRegistry.get(name)
    return cap.description if cap else "（未知能力）"
