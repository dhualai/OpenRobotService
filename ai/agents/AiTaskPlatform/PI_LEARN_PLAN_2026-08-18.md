# Pi Agent Harness 借鉴落地方案（2026-08-18）

> **定位**：从开源 coding agent [earendil-works/pi](https://github.com/earendil-works/pi) 中提炼可落地原语，
> 加固 AiTaskPlatform 现有的多 Agent 编排内核。与 `TASK_AGENT_TARGET_ARCH.md`（架构）、
> `IMPROVE_PLAN_2026-08-16.md`（能力提升）并行。
> **状态**：草案，待评审。
> **原则**：延续「服务端确定性兜底优先」「能不加复杂度就不加」「手册=数据、Agent=策略」。
> 所有改造**只加字段/钩子，不动内核结构**，避免引入新框架。

---

## 0. pi 是什么（一句话定位，避免误判）

pi 是 Mario Zechner (badlogicgames) 的**极简、可自扩展 coding agent 外壳**（对标 Claude Code），分四包：

| 包 | 职责 | 对应我们的东西 |
|----|------|--------------|
| `pi-agent-core` | 有状态 agent 运行时：工具调用 + 事件流 + `before/afterToolCall` 钩子 + `terminate`/`shouldStopAfterTurn` | `BaseCapability` / `Supervisor` |
| `pi-telemetry` | 厂商中立的 **span 遥测契约**（span/attribute/event/status/parent-child + conformance 测试） | `tracing/trace.py` |
| `pi-coding-agent` | CLI + Extensions/Skills/Prompt 模板 + steering/follow-up 消息队列 | 能力注册表 / discuss 追问闭环 |
| `pi-ai` | 多 provider LLM 抽象 | `_llm_client.complete` |

> **关键认知**：pi 的哲学是**故意不做 sub-agents 和 plan mode**（README 原话 "skips features like
> sub agents and plan mode"）。而我们的核心价值**恰恰是** Supervisor + 子 Agent + TodoList plan。
> 所以**不是抄它的 agent 架构，而是借它的工程原语加固我们已有的多 Agent 设计**。

---

## 1. 可借鉴的 4 个原语（按 ROI 排序）

| # | 原语 | 来源 | 补我们什么缺口 | 优先级 |
|---|------|------|--------------|:---:|
| P1 | `terminate` 早停 + 结果钩子 | pi-agent-core | 检索命中已验证根因时**提前收束**，跳过剩余 plan | ⭐ 最高 |
| P2 | `beforeToolCall` / `afterToolCall` | pi-agent-core | 执行前审批钩子 + 结果后处理（盖 verified 章） | ⭐ 高 |
| P3 | span 遥测树 + 类型化 schema | pi-telemetry | TraceBus 从扁平 list → 树，支撑 F5/G6 透明化 + 度量 | 高（工作量大） |
| P4 | steering / follow-up 消息队列 | pi-coding-agent | refine `ask_user` 追问闭环（进行中插话 vs 看完后补充） | 低（可选） |

---

## 2. 现状盘点（改造基线，来自代码实证）

| 位置 | 现状 | 缺口 |
|------|------|------|
| `capabilities/core/base.py:22` `CapabilityResult` | `__slots__ = ("text","meta","ok","error")`，无 terminate/钩子 | 无早停、无结果后处理 |
| `capabilities/core/base.py:102` `__call__` | `配额 → is_available → input_schema → run → except` | 无 before/after 钩子扩展点 |
| `capabilities/core/supervisor.py:399` `_dispatch` | `asyncio.gather`（parallel）或顺序 for（串行），**跑完整个 plan** | 无中途早停；`terminate` 语义缺失 |
| `capabilities/core/supervisor.py:453` `_synthesize` | 简单拼接各结果文本 | 无「已验证根因优先」的排序 |
| `tracing/trace.py:28` `TraceBus` | 扁平 `list[{node,status,ts,...}]`，无父子/attribute/event | 无法回答「时间花哪、每步查了什么」 |
| `handlers/discuss_flow.py:564` 追问闭环 | `CLARIFY_SUGGEST_MARKER` 做「只建议一次」，单通道 | 无 steering/follow-up 区分 |

---

## 3. P1 — `terminate` 早停原语（核心，改动最小收益直接）

### 3.1 目标

让一个能力的结果能说「**证据已足够，别再跑后面的 plan 了**」。典型场景（直接消费
`IMPROVE_PLAN` P2 的 verified 回填）：

> `retrieve_history` 命中一条 `verified=confirmed` 的同车型 + 同错误码根因
> → `terminate=true` → 跳过还在排队、昂贵的 `log_analyze`，直接收束给结论。

这补的是 `Supervisor._dispatch` 现在**没有的控制流原语**——它要么 full plan 跑完、要么
simple→0 派生，**没有中途早停**。

### 3.2 改动点

1. `CapabilityResult` 加 `terminate: bool = False`（`__slots__` + `__init__` + `to_dict()`）。
2. `Supervisor._dispatch` 的串行分支：每跑完一项，若 `res.terminate` 为真 → `break`，不再派剩余 step。
3. `retrieve_history` 能力在命中 `verified=confirmed` 时返回 `terminate=True`（用现有
   `retrieval.py` 里 `_rrf_fusion` 已透出的 `verified` 字段判据）。

### 3.3 语义边界（待确认）

- **串行模式**：terminate → 清晰 break，无歧义。
- **并行模式**：`asyncio.gather` 已同时派发，terminate 无法撤回。落地取**保守语义**：
  terminate 只标记「最终结果已足够」，供上层 `_synthesize` 排序 / 供上层跳过后续 Evaluator 重写；
  不改并行派发本身。是否要进一步做「并行前预检」（如检索先跑、命中才不派日志）留待 P1 验证后再定。

---

## 4. P2 — `beforeToolCall` / `afterToolCall` 钩子

### 4.1 目标

给 `BaseCapability.__call__` 加两个可选钩子（对齐 pi-agent-core）：

- **`beforeToolCall`**：执行前审批，可 `{block: true, reason}` 阻断。这正是 TARGET_ARCH §6b.8 第⑧条
  标注为 future 的「执行前审批钩子」，pi 给出了现成实现形态。
- **`afterToolCall`**：结果 emit 前做后处理。典型：给 `retrieve_history` 结果**盖 `verified` 章**、
  给结果补 `audited` 标记——比现在在 prompt 里拼文本干净。

### 4.2 改动点

- `BaseCapability` 增加两个可覆写方法（默认空实现，零侵入）：
  ```python
  async def before_run(self, kwargs: dict) -> dict | None:  # 返回 {"block":True,"reason":...} 阻断
      return None
  async def after_run(self, result: CapabilityResult) -> CapabilityResult:
      return result
  ```
- `__call__` 在 `run()` 前后分别调用，`block` 时返回 `CapabilityResult.failure(reason)`。
- 不强制任何现有能力实现（保持默认无操作），按需给 `retrieve_history` 加 `after_run` 盖 verified 章。

---

## 5. P3 — span 遥测树（收益最大，工作量最大）

### 5.1 目标

把 `TraceBus` 从扁平 list 升级为 **span 树**（parent/child + attribute + event + status），
对齐 pi-telemetry 的契约：

```
supervisor.run
├─ plan(调度LLM)        attributes: complexity=medium, 派生数=2
├─ log_analyze          events: [建索引, R1, R2]  status: ok  attributes: window_applied=true
└─ retrieve_history     attributes: verified=confirmed, 命中数=3
```

直接命中我们已有的两块目标：
- TARGET_ARCH **F5/G6 透明化 planning**（把每轮调度/查询暴露给前端）——现在 `reasoning_trace`
  只能给 plan/todo，给不出「时间花在哪、每步查了什么」。
- IMPROVE_PLAN **3.3 质量评估集**（数据驱动度量）——span 树是度量「根因命中率/定位路径合理性」的前提。

### 5.2 改动点（分两步，可独立交付）

| 子步 | 内容 | 状态 |
|------|------|------|
| P3a | `TraceBus` 增加 `start_span(name, attributes)` 上下文管理 + `add_event` + `set_status`，保持 `add()/pop()` 兼容旧调用 | 待建 |
| P3b | `Supervisor.run/_dispatch`、各能力用 span 包裹，产出树形 trace 返回前端 | 待建 |

> 建议照抄 pi-telemetry 的 **adapter conformance 测试**思路：为 TraceBus 写一组契约测试
> （父-child 归属、attribute 合并、settlement 语义），换实现时用它兜底。

---

## 6. P4 — steering / follow-up 消息队列语义（可选，低优先）

pi 区分两种排队消息：**steering**（当前 turn 工具跑完就投喂，中途纠偏）vs **follow-up**
（全部工作做完才投喂）。可 refine 我们的 `ask_user` 追问闭环（`discuss_flow.py` 的
`CLARIFY_SUGGEST_MARKER`）：现在只有「只建议一次」的单通道，pi 的语义更干净地表达
「用户在工作进行中插话」vs「用户看完结论后补充」。

> **本版建议跳过**：当前 `CLARIFY_SUGGEST_MARKER` 已满足「只建议一次」的诉求，引入 steering/follow-up
> 会加重 discuss 循环复杂度，不符合「能不加复杂度就不加」。仅作为方向记录，等 P4 追问闭环有真实痛点时再评估。

---

## 7. 落地顺序（Phase 拆解）

| Phase | 内容 | 依赖 | 改动文件 |
|:---:|------|------|---------|
| **P1** | `terminate` 早停（CapabilityResult 加字段 + `_dispatch` 串行 break + retrieve_history 命中即停） | 无 | `base.py` / `supervisor.py` / `retrieve_history.py` |
| **P2** | `before_run`/`after_run` 钩子 + `retrieve_history` 盖 verified 章 | P1 | `base.py` / `retrieve_history.py` |
| **P3a** | TraceBus span 树（兼容旧 `add/pop`） | 无 | `tracing/trace.py` |
| **P3b** | Supervisor/能力接入 span 树 + 前端透出 | P3a | `supervisor.py` / 各能力 / 前端 |
| **P4** | steering/follow-up 队列语义 | P3 | **⏸ 暂缓** |

> 每 Phase 独立可交付、可验证、可回退。P1/P2 是「加字段/钩子」的轻改，优先做；
> P3 是「升级遥测」的中改，独立并行不阻塞 P1/P2。

---

## 8. 风险与取舍

| 风险 | 缓解 |
|------|------|
| `terminate` 误停（检索误判命中就跳日志，漏掉真根因） | 只在 `verified=confirmed`（经回填确认过）时 terminate；`unknown/rejected` 不触发 |
| 并行模式下 terminate 语义不清 | 取保守语义（只排序/跳 Evaluator，不撤回已派发）；预检优化留 P1 验证后 |
| span 树改动牵动现有 `reasoning_trace` 消费方 | P3a 保持 `add/pop` 兼容，旧调用不破坏；P3b 增量加树形字段 |
| 钩子引入增加调用链复杂度 | 默认空实现、零侵入；只在确有需要的 `retrieve_history` 上实现 |

---

## 9. 与既有的关系（边界）

- **不重构内核**：Supervisor / 能力注册表 / Router 结构不动。
- **不引入 pi 框架**：只抄「原语/契约」，不引入 npm 包、不改多 Agent 哲学。
- **明确不抄**：pi 的「不做 sub-agent/plan mode」哲学、Extensions/Pi Packages 分发机制
  （我们 D13 已定「不跨 Agent 共享」）、pi-ai provider 抽象（DeepSeek 够用）。
- **复用**：`CapabilityResult`、`BaseCapability.__call__`、`Supervisor._dispatch`、
  `tracing.TraceBus`、`verified` 回填字段（`retrieval.py`）。

---

*草案（2026-08-18）。P1/P2 改动小、收益直接，建议先做；P3 与 F5/G6 透明化、IMPROVE_PLAN 3.3 度量合并推进。*
