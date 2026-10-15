"""Supervisor — 自主派生子 Agent 的通用编排内核（产品无关）

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
from typing import Any, Callable, Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
from ai.agents.AiTaskPlatform.capabilities.core import usage as cap_usage
from ai.agents.AiTaskPlatform.capabilities.core.supervisor_todo import TodoList, TodoItem
from ai.agents.AiTaskPlatform.tracing import TraceBus

logger = get_logger("TASK_AGENT")

# ── 程序强制护栏（服务端优先，不信任 LLM）──
MAX_SUB_TASKS = 5          # 一次最多派生子任务数（防无限分叉）；与 _CONCURRENCY 配合：可派 5 个，但最多同时跑 _CONCURRENCY 个
SOFT_LIMIT = 2             # 调度 LLM 输出解析失败时的重试次数上限（浅则重试，达到上限走兜底）
_CONCURRENCY = 3           # 同时最多并行执行几个子任务（asyncio.Semaphore，防拉爆 API/限流）
_MAX_ROUNDS = 2            # 规划轮数上限：初始 + 追规一次。轮数越多越贵（每轮一次规划 LLM + 可能多派能力）
_RESULT_SNIPPET_CHARS = 400   # 追规时每个能力结果喂回去的字符上限（防把长文本再灌一遍）
_EXECUTED_BLOCK_CHARS = 2000  # 「本轮已执行」整段的字符上限


def _plan_waves(plan: list[dict]) -> list[list[tuple[int, dict]]]:
    """把 plan 按 `after` 依赖分成若干波；同波内并发跑。

    **没有任何步骤写 `after` 时，沿用改造前的默认顺序**：非 log_analyze 先跑、
    log_analyze 后跑（历史方案已验证就别再读昂贵日志）。这条默认用同一套机制表达，
    不是另一条代码路径。

    一旦有步骤显式写了 `after`，就完全按它来（不写 after 的步骤进第一波）。
    依赖成环或指向未排的能力时，剩下的**并作一波照跑** —— 宁可不按序，也不卡死。
    """
    indexed = list(enumerate(plan))
    any_explicit = any(s.get("after") for _, s in indexed)

    deps: dict[int, set] = {}
    for i, s in indexed:
        dep = set(s.get("after") or [])
        if not any_explicit and s.get("capability") == "log_analyze":
            dep = {c for _, c in ((j, t.get("capability")) for j, t in indexed)
                   if c and c != "log_analyze"}
        deps[i] = dep

    waves: list[list[tuple[int, dict]]] = []
    done: set = set()
    remaining = dict(deps)
    while remaining:
        ready = [i for i, dep in remaining.items() if not (dep - done)]
        if not ready:                      # 成环 / 依赖落空 → 剩下的并一波，别卡死
            logger.warning(f"[supervisor] after 依赖无法满足，剩余 {len(remaining)} 步并作一波")
            ready = list(remaining)
        waves.append([indexed[i] for i in sorted(ready)])
        for i in ready:
            done.add(plan[i].get("capability"))
            remaining.pop(i)
    return waves


# 原生工具调用：让模型用**工具参数**提交计划（参数由接口解析成 dict），
# 绕开「模型吐的 JSON 不老实」那一层。失败一律回退 JSON 路径，见 Supervisor._plan。
_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": "提交本次排查的调度计划（必须调用本工具，不要输出文字）",
        "parameters": {
            "type": "object",
            "properties": {
                "complexity": {"type": "string", "enum": ["simple", "medium", "complex"]},
                "reasoning": {"type": "string", "description": "简短理由"},
                "ask_user": {"type": "boolean"},
                "questions": {"type": "array", "items": {"type": "string"}},
                "plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "capability": {"type": "string", "description": "必须来自可用能力清单"},
                            "goal": {"type": "string"},
                            "parallel": {"type": "boolean"},
                            "after": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "本步要等哪些能力先跑完（可选）",
                            },
                            "window_minutes": {"type": "integer"},
                        },
                        "required": ["capability"],
                    },
                },
            },
            "required": ["complexity", "plan"],
        },
    },
}


def _max_run_seconds() -> float:
    """本次 run 的墙上时钟上限（热生效）。取不到就用默认值 —— 配置问题不该打断调度。"""
    try:
        from ai.config import supervisor_max_run_seconds
        return float(supervisor_max_run_seconds())
    except Exception:
        return 300.0


def _cap_succeeded(res) -> bool:
    """该能力这一轮是否**拿到了东西**。

    只有「ok 且有正文」才算成功。失败 / 空输出 / 配额拦下都算没拿到 ——
    追规那轮据此决定哪些还能再派一次（见 run() 的 caps_round）。
    """
    return bool(
        isinstance(res, dict)
        and res.get("ok", True)
        and str(res.get("text") or "").strip()
    )


def _upstream_block(step: dict, prev_results: dict) -> str:
    """该步骤声明依赖（`after`）的上游能力结果，供它接着分析。

    没有 `after` 的步骤拿不到 —— 不给它灌一堆无关的上游结论，避免把 query 冲淡。
    """
    deps = step.get("after") or []
    if not deps or not prev_results:
        return ""
    parts: list[str] = []
    for cap in deps:
        text = str((prev_results.get(cap) or {}).get("text") or "").strip()
        if not text:
            continue
        if len(text) > _RESULT_SNIPPET_CHARS:
            text = text[:_RESULT_SNIPPET_CHARS] + "…（已截断）"
        parts.append(f"[{cap}]\n{text}")
    return "\n\n".join(parts)[:_EXECUTED_BLOCK_CHARS]


def _executed_block(plan_all: list, results: dict) -> str:
    """追规那轮的「本轮已执行」段：只说**结果**，不带能力的内部过程。

    过程（日志子 Agent 的多轮、工具内部步骤）走 progress_emitter / span 树，
    是给前端过程区看的，**不进任何 prompt**。这里只喂「派了什么 + 拿到了什么」，
    让规划器知道还缺什么，而不是重复派同样的能力。
    """
    if not plan_all and not results:
        return ""
    lines = ["## 本轮已执行"]
    for step in plan_all:
        cap = step.get("capability") or ""
        goal = step.get("goal") or ""
        res = results.get(cap) or {}
        text = str(res.get("text") or "").strip()
        if _cap_succeeded(res):
            mark = "（已成功，不要重复派）"
        else:
            # 明确告诉规划器「这条还能再试」——失败的重试不会覆盖任何证据，
            # 而换一个角度再查一次正是追规这一轮的意义。
            mark = f"（没拿到结果：{res.get('error') or '无输出'}，可换个说法再派一次）"
        if len(text) > _RESULT_SNIPPET_CHARS:
            text = text[:_RESULT_SNIPPET_CHARS] + "…（已截断）"
        lines.append(f"- {cap}（{goal}）：{text or '（空）'} {mark}")
    return "\n".join(lines)[:_EXECUTED_BLOCK_CHARS]


@dataclass
class SupervisorDecision:
    """Supervisor 调度决策（程序解析 LLM 输出后的结构化结果）。"""
    complexity: str = "simple"           # simple | medium | complex
    reasoning: str = ""
    plan: list[dict] = field(default_factory=list)  # [{capability, goal, parallel}]
    ask_user: bool = False
    questions: list[str] = field(default_factory=list)  # ask_user=true 时，要问用户的具体澄清问题（闭环 P4）

    def is_simple(self) -> bool:
        return self.complexity == "simple"


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
    return _decision_from_dict(data)


def _decision_from_dict(data: dict) -> Optional[SupervisorDecision]:
    """规范化后的决策 dict → SupervisorDecision。

    单独抽出来是因为**原生工具调用**拿到的参数已经是 dict（由接口解析），
    不需要再走 `_parse_decision` 那套「从模型文本里抠 JSON」的容错 —— 两条路
    共用这一步，形状才保证一致。
    """
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
            for extra_key in ("window_minutes", "before_minutes", "after_minutes", "occurred_at", "params"):
                if extra_key in p and p[extra_key] is not None:
                    item[extra_key] = p[extra_key]
            # after：该步骤要等哪些能力跑完（可选）。给了就能让后波看到前波的结果，
            # 例如「日志读出型号后再去查该型号的 KB」。见 _plan_waves。
            _after = p.get("after")
            if isinstance(_after, str):
                _after = [_after]
            if isinstance(_after, list):
                _after = [str(x).strip() for x in _after if str(x or "").strip()]
                if _after:
                    item["after"] = _after
            clean_plan.append(item)
    # questions（ask_user=true 时问用户的澄清问题；兼容 string / list 两种形态）
    q = data.get("questions") or []
    if isinstance(q, str):
        q = [s for s in (s_.strip() for s_ in q.split("\n")) if s]
    if not isinstance(q, list):
        q = []
    questions = [str(x).strip() for x in q if str(x).strip()]
    return SupervisorDecision(
        complexity=str(data.get("complexity", "simple")),
        reasoning=str(data.get("reasoning", "")),
        plan=clean_plan,
        ask_user=bool(data.get("ask_user", False)),
        questions=questions,
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

    # questions（字段级兜底：尽力捞 JSON 数组里的字符串项）
    questions = []
    qm = _re.search(r'"questions"\s*:\s*\[[^\]]*\]', text, _re.DOTALL)
    if qm:
        questions = [s.strip(" \"'\n") for s in _re.findall(r'"([^"]+)"', qm.group(0)) if s.strip()]

    if not data.get("complexity") and not plan:
        return None
    return SupervisorDecision(
        complexity=str(data.get("complexity", "simple")),
        reasoning="",
        plan=data.get("plan", []),
        ask_user=bool(data.get("ask_user", False)),
        questions=questions,
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

    def _bus(self) -> TraceBus:
        ctx = self._runtime_ctx or {}
        bus = ctx.get("trace_bus")
        if isinstance(bus, TraceBus):
            return bus
        bus = TraceBus()
        ctx["trace_bus"] = bus
        self._runtime_ctx = ctx
        return bus

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
            '"plan":[{"capability":"<能力名，必须来自可用能力清单>","goal":"...","parallel":bool,'
            '"after":["<可选：本步要等哪些能力先跑完>"],"window_minutes":<可选，仅log_analyze>}],'
            '"ask_user":bool,"questions":["..."]}\n'
            "规则：\n"
            "- 任务简单（纯知识问答、无多领域线索）→ complexity=simple，plan 为空\n"
            "- 单一领域线索 → complexity=medium，plan 含 1 项\n"
            "- 多领域线索交叉 → complexity=complex，plan 含多项，parallel 合理设 true\n"
            "- capability 只能从可用能力清单里选，严禁凭空命名\n"
            "- **after（可选）**：某步必须**拿着另一步的结论**才能做时，在 after 里写那个能力名"
            "（如「日志分析读出机型/错误码后再去查知识库」→ 知识库那步写 after:[\"log_analyze\"]）。"
            "写了的步骤会等依赖跑完，并**自动拿到依赖的结果**作为输入。\n"
            "- 若上方给了「本轮已执行」段：**已成功的**不要重复派；标着「可换个说法再派一次」的"
            "说明上一轮没拿到结果，可以再派一次（换个更具体的 goal）。都没有就说明够了。\n"
            "- after 不写 = 可以立刻并行跑。**只有当确实存在先后依赖时才写**，"
            "无依赖地串起来只会白白拖长耗时。\n"
            "- 若派 log_analyze 且故障属缓慢累积/日志稀疏，可在 window_minutes 指定更长的前因窗口（默认15即可）\n"
            "- retrieve_history 与 log_analyze 同时派时，程序会先跑历史方案；已验证命中则跳过日志分析\n"
            "- **若关键信息缺失（如故障发生时间/是否可复现/变更了什么/报错现场）导致无法可靠排查，"
            "且无能力/无充足依据可先派发硬性定位 → 设 ask_user=true，并在 questions 里列出"
            "需要向用户确认的具体问题（每一项都应是可直接回答的高价值问题，不要笼统）**；\n"
            "- ask_user=true 时仍可同时派 plan（先用已有依据尽量排查 + 顺带询问缺失项）；"
            "仅当 plan 为空时才作为纯澄清请求返回。\n"
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
                todo 的实时进展推给前端动态执行过程展示。

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
        self._runtime_ctx.setdefault("round_supplements", [])
        cap_usage.ledger(self._runtime_ctx)  # 本次请求的配额台账（幂等）
        # 本次请求里已用满的能力，从可选集里物理摘掉（同一 runtime_ctx 再跑一轮时命中）
        caps = cap_usage.drop_exhausted(self._runtime_ctx, caps)
        if not isinstance(self._runtime_ctx.get("trace_bus"), TraceBus):
            self._runtime_ctx["trace_bus"] = TraceBus()
        self._on_progress = on_progress  # 实时进度回调（逐项能力推送，供前端动态展示）

        def _emit(phase: str, td: dict) -> None:
            if self._on_progress is None:
                return
            try:
                self._on_progress({**td, "phase": phase, "capability": td.get("capability", "")})
            except Exception:
                pass

        bus = self._bus()

        with bus.start_span("supervisor.run"):
            todo = TodoList()
            results: dict = {}
            plan_all: list = []
            rounds: list[dict] = []
            first_complexity = None
            first_decision = None
            ask_user = False
            questions: list = []
            # 墙上时钟安全阀：只挡「开始新的一轮」，不打断已经在跑的能力（那会丢证据）。
            _deadline = t0 + _max_run_seconds()

            # 有界多轮：初始规划 → 派发 → 看结果 → 再追规一次（_MAX_ROUNDS）。
            # 原来只规划一次、结果不回流 —— KB 没命中 / 日志没读出根因时没有第二次机会。
            for rnd in range(_MAX_ROUNDS):
                if results and _time.perf_counter() > _deadline:
                    rounds.append({"round": rnd + 1, "stop": "超时收束"})
                    logger.warning(
                        f"[supervisor] 已达墙上时钟上限（{_max_run_seconds()}s），不再开始新一轮"
                    )
                    break
                # 每轮重算可派集：用满的摘掉，**已经成功**的也摘掉。
                # 成功的必须排除：results 以**能力名**为 key，同一能力跑两次后者会覆盖
                # 前者，而上层逐 key 读 text —— 覆盖等于吞掉证据。
                # **失败的可以再派**（C）：失败的结果没有 text，重试它不会覆盖任何证据，
                # 而「换个角度再查一次」正是追规这一轮存在的意义。
                caps_round = [c for c in cap_usage.drop_exhausted(self._runtime_ctx, caps)
                              if not _cap_succeeded(results.get(c))]
                if not caps_round:
                    rounds.append({"round": rnd + 1, "stop": "无可派能力"})
                    break

                ctx_round = task_context
                if rnd > 0:
                    ctx_round = f"{task_context}\n\n{_executed_block(plan_all, results)}"

                with bus.start_span("plan"):
                    decision = await self._plan(ctx_round, caps_round, max_sub_tasks)
                    bus.set_attribute("complexity", decision.complexity)
                    bus.set_attribute("派生数", len(decision.plan or []))
                    if rnd > 0:
                        bus.set_attribute("追规", True)

                if first_decision is None:
                    first_decision, first_complexity = decision, decision.complexity
                # 澄清问题以**首轮**提出为准（最贴近用户原话），不被后续轮覆盖
                if decision.ask_user and decision.questions and not ask_user:
                    ask_user, questions = True, list(decision.questions)

                if decision.is_simple() or not decision.plan:
                    rounds.append({"round": rnd + 1, "stop": "规划器判定无需再派"})
                    break

                for step in decision.plan:
                    todo.add(step.get("goal") or step.get("capability"),
                             capability=step.get("capability", ""))
                plan_all.extend(decision.plan)

                if rnd == 0:
                    await self._consume_injects(todo, _emit)
                got = await self._dispatch(decision.plan, todo, max_sub_tasks, _emit)
                results.update(got)
                rounds.append({
                    "round": rnd + 1,
                    "planned": [s.get("capability") for s in decision.plan],
                })

                # 历史方案已验证（terminate）→ 硬停，别白跑下一轮昂贵能力
                if any(_result_terminated(r) for r in got.values()):
                    rounds[-1]["stop"] = "历史方案已验证"
                    break
                await self._consume_injects(todo, _emit)

            note = cap_usage.notes(self._runtime_ctx)
            # 耗时/轮次观测：想给调度定「成本预算」时，先看这条的分布，别凭想象设数。
            _elapsed_ms = round((_time.perf_counter() - t0) * 1000)
            logger.info(
                f"[supervisor] 本轮耗时 {_elapsed_ms}ms 轮次={len(rounds)} "
                f"派发={sorted(results)} 停因={[r.get('stop') for r in rounds if r.get('stop')]}"
            )
            return {
                # complexity 取**首轮**：它描述问题本身的复杂度，不该被次轮的 simple 覆盖
                "complexity": (first_complexity or "simple"),
                "plan": plan_all,
                "ask_user": ask_user,
                "questions": questions,
                "todo": todo.to_dict_list(),
                "results": results,
                "final_text": self._synthesize(results, note) if results else "",
                # 配额说明单独给一个键：docstring 写着「必须进正文」，但 final_text 没有
                # 消费者（discuss_flow 从不读它）→ 上层要用 note，别指望它出现在 results 里。
                "note": note,
                "rounds": rounds,
                "elapsed_ms": round((_time.perf_counter() - t0) * 1000),
                "_decision": (first_decision.__dict__ if first_decision else {}),
                "spans": bus.tree(),
            }

    # ── 调度决策 ──
    async def _plan(self, task_context: str, caps: list[str], max_sub_tasks: int) -> SupervisorDecision:
        """取调度决策：**优先原生工具调用**，拿不到再走原来的 JSON 路径。

        两条路都产出同一种 SupervisorDecision（共用 `_decision_from_dict`），
        所以后续的护栏/派发/进度完全不受影响。工具调用不可用时行为与改造前一致。
        """
        decision = await self._plan_via_tool(task_context, caps)
        if decision is None:
            decision = await self._plan_via_json(task_context, caps)
        if decision is None:
            logger.warning("[supervisor] 调度解析多次失败，按 simple 兜底")
            return SupervisorDecision(complexity="simple", reasoning="调度解析多次失败，兜底为 simple")
        # 程序强制护栏（服务端优先，不信任 LLM）
        return self._guard_decision(decision, caps, max_sub_tasks)

    async def _plan_via_tool(self, task_context: str, caps: list[str]) -> Optional[SupervisorDecision]:
        """原生工具调用取计划：参数由接口解析成 dict，不需要从模型文本里抠 JSON。

        **只调一轮**：模型把计划当作最终产物通过 `submit_plan` 提交，不涉及「工具结果回传」，
        所以也不会碰到 DeepSeek「tools + 思考」那条 reasoning_content 多轮回传协议。

        任何失败（llm 不支持工具调用 / 调用抛错 / 模型没调工具 / 参数不是 dict）→ None，
        由 `_plan` 回退 JSON 路径 —— 这条回退保证新路径不可能比改造前更差。
        """
        complete_with_tools = getattr(self._llm, "complete_with_tools", None)
        if complete_with_tools is None:
            return None
        try:
            resp = await complete_with_tools(
                tools=[_PLAN_TOOL],
                prompt=self._build_plan_prompt(task_context, caps),
                system_prompt=(
                    "你是一个严谨的排查任务调度器。**必须调用 submit_plan 工具**提交计划，"
                    "不要输出任何文字。"
                ),
                max_tokens=600,
                temperature=0.0,
            )
        except Exception as e:
            logger.info(
                f"[supervisor] 原生工具调用取计划失败（{type(e).__name__}: {e}），回退 JSON 路径"
            )
            return None

        for tc in (resp or {}).get("tool_calls") or []:
            if tc.get("name") != "submit_plan":
                continue
            args = tc.get("arguments")
            if isinstance(args, dict) and (args.get("plan") is not None or args.get("complexity")):
                return _decision_from_dict(args)
        logger.info("[supervisor] 模型没有调用 submit_plan，回退 JSON 路径")
        return None

    async def _plan_via_json(self, task_context: str, caps: list[str]) -> Optional[SupervisorDecision]:
        """原来的路径：让模型吐 JSON，多层容错解析；全失败返回 None（由 _plan 兜底）。

        （两层容错——`_parse_decision` 的抠 JSON + 这里的重试——之所以留着，是因为
          中转站/换模型时模型不一定支持工具调用；它同时也是工具路径的回退网。）
        """
        prompt = self._build_plan_prompt(task_context, caps)
        for attempt in range(SOFT_LIMIT + 1):
            raw = await self._llm.complete(
                prompt=prompt,
                system_prompt="你是一个严谨的排查任务调度器，只输出 JSON。",
                max_tokens=300,
                temperature=0.0,
            )
            decision = _parse_decision(raw)
            if decision is not None:
                return decision
            logger.warning(
                f"[supervisor] 调度输出解析失败 (attempt {attempt + 1}/{SOFT_LIMIT + 1}); "
                f"raw={repr((raw or '')[:300])}"
            )
        return None

    def _guard_decision(self, d: SupervisorDecision, caps: list[str], max_sub_tasks: int) -> SupervisorDecision:
        """强护栏：
          - simple 强制 0 派生
          - plan 里的 capability 必须在可用清单内（否者丢弃 + 记 trace）
          - 同一能力不能超过它声明的每次请求上限（超出的步骤直接不排）
          - 派生数不能超过 max_sub_tasks
        """
        valid_caps = set(caps)
        if d.is_simple():
            d.plan = []  # 强制 0 派生，防过度设计
            return d
        kept = []
        per_cap: dict[str, int] = {}
        for step in d.plan:
            cap_name = step["capability"]
            if cap_name not in valid_caps:
                logger.warning(f"[supervisor] 丢弃未知能力: {cap_name}")
                continue
            lim = cap_usage.limit_of(cap_name)
            if lim is not None and per_cap.get(cap_name, 0) >= lim:
                # 机制层直接不排（不是靠 prompt 求 LLM 少调）；同一能力只记一条说明
                cap_usage.block(self._runtime_ctx, cap_name,
                                f"计划里排了第 {per_cap[cap_name] + 1} 次，已丢弃")
                logger.info(f"[supervisor] {cap_name} 计划步骤超过上限 {lim} 次，丢弃多余步骤")
                continue
            per_cap[cap_name] = per_cap.get(cap_name, 0) + 1
            kept.append(step)
        # 数量上限
        kept = kept[:max_sub_tasks]
        # after 只允许指向**本 plan 里真实存在**的能力：指向没排的能力会让那一波永远等
        # 不到（靠 _plan_waves 的环兜底也只是变成并跑），不如这里直接摘干净。
        present = {s.get("capability") for s in kept}
        for step in kept:
            if "after" in step:
                dep = [a for a in step["after"] if a in present and a != step.get("capability")]
                if dep:
                    step["after"] = dep
                else:
                    step.pop("after", None)
        d.plan = kept
        return d

    # ── 派生子任务（支持并行/串行 + 并发上限 + 早停）──
    async def _dispatch(self, plan: list[dict], todo: TodoList, max_sub_tasks: int, emit: Optional[Callable] = None) -> dict:
        """按 `_plan_waves` 分波派发；后波能拿到前波的结果（issues.md #25-B）。

        为什么要分波：历史方案已验证（terminate）就不必再读昂贵的日志。
        为什么要让后波看到前波：日志分析读出关键信息后，才谈得上拿它去查该能力对应的
        KB/排查树/历史 —— 改造前后波看不到前波，这条链是断的。

        按原 plan 下标对齐 todo，避免早停时标错项。
        """
        results: dict = {}
        for wave in _plan_waves(plan):
            got = await self._dispatch_group(wave, todo, emit, results)
            results.update(got)
            if any(_result_terminated(r) for r in got.values()):
                # 历史方案已验证 → 后面的波整体跳过（与改造前的早停语义一致）
                self._skip_remaining_waves(plan, results, todo, emit)
                break
        return results

    def _skip_remaining_waves(self, plan, results, todo, emit) -> None:
        """把还没跑过的步骤标成「已跳过：历史方案已验证」并推进度。"""
        bus = self._bus()
        for i, step in enumerate(plan):
            cap = step.get("capability")
            if cap in results:
                continue
            if i >= len(todo._items):
                continue
            item = todo._items[i]
            if item.status == "completed":
                continue
            with bus.start_span(cap or "capability"):
                bus.set_status("skipped", reason="历史方案已验证")
            todo.mark_done(item.id, result="已跳过：历史方案已验证（早停）")
            if emit is not None:
                emit("done", {
                    "id": item.id,
                    "description": item.description or cap or "",
                    "status": "completed",
                    "capability": cap or "",
                })

    async def _dispatch_group(
        self,
        indexed_plan: list[tuple[int, dict]],
        todo: TodoList,
        emit: Optional[Callable],
        prev_results: Optional[dict] = None,
    ) -> dict:
        sem = asyncio.Semaphore(_CONCURRENCY)
        prev_results = prev_results or {}

        async def _run_one(step: dict, todo_item: TodoItem):
            cap_name = step["capability"]
            cap = CapabilityRegistry.get(cap_name)
            if cap is None or not cap.is_available():
                todo.mark_done(todo_item.id, result=f"能力不可用: {cap_name}")
                return cap_name, {"ok": False, "error": f"能力不可用: {cap_name}", "text": ""}
            if not cap_usage.claim(self._runtime_ctx, cap_name):
                # 机制层硬收口：超出本次请求上限的能力不再执行（不靠 prompt 求 LLM）
                reason = f"已达本次请求调度上限（{cap_usage.limit_of(cap_name)} 次），未再派发"
                cap_usage.block(self._runtime_ctx, cap_name, "超出上限的派发已拦下")
                logger.info(f"[supervisor] {cap_name} {reason}，跳过执行")
                todo.mark_done(todo_item.id, result=reason)
                # meta.blocked 是**独立标记**：这是机制层主动收敛，不是能力执行失败。
                # 少了它上层只能看到 ok=False，会把「主动不再重复分析」渲染成「分析失败」。
                return cap_name, {
                    "ok": False,
                    "error": reason,
                    "text": "",
                    "meta": {"blocked": "quota"},
                }
            todo.mark_in_progress(todo_item.id)
            if emit is not None:
                desc = todo_item.description
                # log_analyze：过程区展示真实文件名，避免 Supervisor goal 写成「分析 TASK-MANAGER」却实际在读 DYNAMIC_MAP
                if cap_name == "log_analyze":
                    try:
                        from pathlib import Path as _P
                        lp = self._runtime_ctx.get("log_path") or ""
                        occ = self._runtime_ctx.get("occurred_at") or ""
                        if lp:
                            desc = f"分析日志文件：{_P(lp).name}"
                            if occ:
                                desc += f"（锚点 {occ}）"
                            todo_item.description = desc
                    except Exception:
                        pass
                emit("running", {"id": todo_item.id, "description": desc, "status": "in_progress", "capability": cap_name})
            try:
                async with sem:
                    kwargs = {"query": step.get("goal", "")}
                    # 声明了 after 的步骤，把它依赖的上游结果接在自己的 query 后面，
                    # 让「日志读出型号 → 再去查该型号的 KB」这类链条成立。
                    _up = _upstream_block(step, prev_results)
                    if _up:
                        kwargs["query"] = (
                            f"{kwargs['query']}\n\n## 上游能力的结论（据此继续）\n{_up}"
                        ).strip()
                    extra = "\n".join(self._runtime_ctx.get("round_supplements") or [])
                    if extra:
                        kwargs["query"] = f"{kwargs['query']}\n\n工程师本轮补充:\n{extra}".strip()
                    kwargs.update(self._runtime_ctx)
                    for extra_key in ("window_minutes", "before_minutes", "after_minutes", "occurred_at", "params"):
                        if extra_key in step and step[extra_key] is not None:
                            kwargs[extra_key] = step[extra_key]
                    bus = self._bus()
                    with bus.start_span(cap_name):
                        result = await cap(**kwargs)
                        self._annotate_span(bus, result)
            except Exception as e:
                result = {"ok": False, "error": f"{type(e).__name__}: {e}", "text": ""}
            res_dict = result.to_dict() if hasattr(result, "to_dict") else (result if isinstance(result, dict) else {"text": str(result), "ok": True})
            todo.mark_done(todo_item.id, result=str(res_dict.get("text", ""))[:80])
            if emit is not None:
                emit("done", {"id": todo_item.id, "description": todo_item.description, "status": "completed", "capability": cap_name})
            await self._consume_injects(todo, emit)
            return cap_name, res_dict

        if any(step.get("parallel") for _, step in indexed_plan) and len(indexed_plan) > 1:
            tasks = [_run_one(step, todo._items[i]) for i, step in indexed_plan]
            done = await asyncio.gather(*tasks, return_exceptions=True)
            results = {}
            for n, item in enumerate(done):
                cap_name = indexed_plan[n][1]["capability"]
                if isinstance(item, BaseException):
                    results[cap_name] = {"ok": False, "error": str(item)}
                    continue
                name, res = item
                results[name] = res
            await self._consume_injects(todo, emit)
            return results

        results = {}
        for i, step in indexed_plan:
            cap_name, res = await _run_one(step, todo._items[i])
            results[cap_name] = res
            if _result_terminated(res):
                break
        return results

    async def _consume_injects(self, todo: TodoList, emit: Optional[Callable] = None) -> None:
        """能力边界读取「插入本轮」邮箱：不中断当前步骤，只纳入后续能力/最终答复。"""
        ctx = self._runtime_ctx or {}
        current = ctx.get("current_task") or {}
        task_id = str(current.get("task_id") or ctx.get("task_id") or "")
        if not task_id:
            return
        try:
            from ai.agents.AiTaskPlatform.runtime.inject_mailbox import drain
            texts = await drain(task_id)
        except Exception as e:
            logger.warning(f"[supervisor] inject drain 失败: {e}")
            return
        if not texts:
            return
        bag = ctx.setdefault("round_supplements", [])
        bag.extend(texts)
        preview = "；".join(t.replace("\n", " ")[:40] for t in texts)
        item = todo.add(f"本轮补充：{preview[:60]}", capability="inject")
        todo.mark_done(item.id, result="已纳入本轮")
        if emit is not None:
            emit("done", {
                "id": item.id,
                "description": item.description,
                "status": "completed",
                "capability": "inject",
            })

    @staticmethod
    def _annotate_span(bus: TraceBus, result) -> None:
        """把能力结果的关键字段写到当前 span。"""
        res_dict = result.to_dict() if hasattr(result, "to_dict") else (
            result if isinstance(result, dict) else {"ok": True}
        )
        if not res_dict.get("ok", True):
            bus.set_status("error")
        if res_dict.get("terminate"):
            bus.set_attribute("terminate", True)
        meta = res_dict.get("meta") or {}
        if not isinstance(meta, dict):
            return
        for key, alias in (
            ("count", "命中数"),
            ("confirmed", "confirmed"),
            ("verified", "verified"),
            ("window_applied", "window_applied"),
            ("queries", "queries"),
        ):
            if key in meta and meta[key] is not None:
                bus.set_attribute(alias, meta[key])

    # ── 汇总 ──
    @staticmethod
    def _synthesize(results: dict, notes: str = "") -> str:
        """把各子任务结果拼成最终文本（简单拼接，供上层 LLM 继续整理）。

        notes：机制层说明（如某能力已达本次请求调度上限、未再派发）。必须进正文——
        缺了它，整理答复的模型会以为还有更深的分析可做。
        """
        parts = []
        items = list(results.items())
        items.sort(key=lambda kv: (not _result_terminated(kv[1]), kv[0]))
        for cap_name, res in items:
            if isinstance(res, dict):
                text = res.get("text", "")
                ok = res.get("ok", True)
                tag = "✅" if ok else "⚠️"
                if text:
                    parts.append(f"[{cap_name}] {tag} {text}")
                elif not ok:
                    parts.append(f"[{cap_name}] ⚠️ {res.get('error', '执行失败')}")
        body = "\n\n".join(parts)
        return f"{body}\n\n{notes}" if notes else body


def _result_terminated(res) -> bool:
    if not isinstance(res, dict):
        return bool(getattr(res, "terminate", False))
    if res.get("terminate"):
        return True
    meta = res.get("meta") or {}
    return bool(meta.get("terminate"))


def _cap_desc(name: str) -> str:
    """获取能力描述（供调度 prompt 用）。"""
    cap = CapabilityRegistry.get(name)
    return cap.description if cap else "（未知能力）"
