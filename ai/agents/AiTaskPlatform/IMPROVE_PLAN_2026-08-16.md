# AiTaskPlatform 能力提升落地方案（2026-08-16）

> **定位**：这是三大优先项的落地设计稿，与 `TASK_AGENT_TARGET_ARCH.md`（架构）并行。
> **状态**：草案，待评审。
> **原则**：延续「服务端确定性兜底优先」「手册=数据、Agent=策略」「能不加复杂度就不加」。
> 所有改造均**先复用现有字段/钩子**（`ask_user` / `needs_more_info` / `BaseCapability` / Supervisor），
> 避免引入新框架。

---

## 0. 现状盘点（改造基线，来自代码实证）

### 0.1 「追问」的脚手架已存在但**未形成闭环** ⚠️（核心发现）

| 字段 | 存在位置 | 现状态 |
|------|---------|:---:|
| `SupervisorDecision.ask_user` | `supervisor.py` | ✅ 被解析、被返回，但 **dispatch 不理会** —— 无论 `ask_user=true` 都照常派生子任务 |
| `SolutionDraft.needs_more_info` | `schemas.py` / `solution_io.py` | ✅ 字段存在，但只由 `confidence<0.5` 机械推导，**无后续追问行为** |
| `diagnose` 返回值 `needs_more_info` | `diagnose_flow.py:327` | ✅ 同样 `conf<0.5`，**前端无追问交互** |

**结论**：系统已经"说出"了需要更多信息，但没有**对话闭环**把"缺口 → 定向提问 → 回复喂回分析"串起来。这正是用户要补的核心。

### 0.2 知识闭环是「扁平文本」，缺结构化与验证回填 ⚠️

`submit()` 写 Qdrant 的内容是：
```python
solution_text = f"根因: {root_cause_analysis}\n步骤: {'; '.join(suggested_actions)}"
```
- ❌ **无结构化根因**（现象/根因/解法/验证状态/车型/错误码/严重度分字段）
- ❌ **无验证回填**（提交后不追踪"这个方案后来被验证/推翻/复现了吗"）
- → 历史方案检索只能拿到大段文本，缺乏"可直接采信 + 判别能否套用 + 可信度"的信息

### 0.3 车型级经验记忆缺失 ⚠️

- `TodoList` 只存活单次会话（记忆已有记录）
- `ai_summary` 只是文本摘要，**没有被结构化**成"车型 × 错误码簇 → 历史根因 + 解法 + 验证状态"的查表
- 每次排查都从零开始，不记得"这个车型以前哪几个坑"

---

## 1. 优先级一：知识闭环质量 — 结构化根因 + 验证回填

### 1.1 目标

把 `submit → Qdrant` 从"存文本"升级为"存结构化、可验证、可复用的经验"，并让历史方案检索能命中"**经过验证的根因**"。

### 1.2 结构化根因 schema（新增，放 `schemas.py`）

```python
@dataclass
class ResolvedRootCause:
    # —— 现象侧 ——
    symptom: str                # 现象/症状（如"避让后车不动"）
    robot_type: str             # 车型（如 XNA-169）
    error_codes: list[str]      # 错误码簇（如 ["last_node_index校验失败"]）
    scenario: str               # 场景/触发条件
    # —— 根因侧 ——
    root_cause: str             # 根因（结构化假设→验证路径→定论）
    root_cause_type: str        # 常见分类：版本缺陷/配置错误/环境问题/竞态/未知
    evidence: str               # 支撑证据（日志行/时间窗/复现）
    # —— 解法侧 ——
    resolution: str
    resolution_actions: list[str]
    # —— 元信息 ——
    confidence: float
    severity: str               # 影响面/严重度
    is_common_bug: bool         # 是否通用缺陷（非个案）→ 建议提 issue
    verified: str               # unknown|confirmed|rejected|recurred（验证状态，供回填）
```

### 1.3 写入（升级 `submit`）

- `submit` 在已有 `_index_solution` 前，先让 LLM 从 `draft + task context` 结构化成 `ResolvedRootCause`（一次小调用，`temp=0`）。
- 写入 Qdrant 的 payload = `ResolvedRootCause` 结构化字段（保留现有 `solution_text` 兼容检索），**并新增 `root_cause_type`/`verified=unknown`/`error_codes`/`robot_type` 作为过滤维度**。
- `verified` 初始 `unknown`，留给回填。

> **✅ P1 已实现（2026-08-16）**：`ResolvedRootCause` dataclass（schemas.py）+
> `_parse_struct_root`/`_extract_structured_root_cause`（solution_flow.py，temp=0 小调用、失败回退不阻断）+
> `index_task_resolution` payload 新增 `root_cause_type/error_codes/severity/is_common_bug/verified`
> （默认 safe，兼容旧数据）+ `pipeline._index_solution` 透传。全量 py_compile 通过 + 一次性验证脚本通过
> （含 unknown 归一化，避免中英文混用影响下游过滤）。

### 1.4 验证回填（核心新增，闭环）

**触发扫描**：复用现有 `summarize` 后台定时节奏（或并入同一 worker），对**已结案且有结构化根因**的工单做轻量回填：

| 信号 | 判定 | 回填 `verified` |
|------|------|:---:|
| 后续讨论出现"解决了/好了/回退后正常" | 方案被验证 | `confirmed` |
| 后续讨论出现"还是不行/没解决/复现" | 方案被推翻或复发 | `rejected` / `recurred` |
| 同车型 + 同 error_code 在 N 天内再次建单 | 复发 | `recurred` |

> - 回填用**确定性关键词 + LLM 判读**双轨（服务端优先：关键词先兜底）。
> - **检索侧消费**：历史方案检索时，`verified=confirmed` 的方案权重上调并标注"经验证"，`rejected`/`recurred` 降权或标注"已被推翻"。
> - 这样避免"看似结案其实错"的样本污染后续排查。

> **✅ P2 已实现（2026-08-16）**：
> - `services/verified_backfill.py`：`_detect_signal`（确定性关键词，confirm/reject/recur）+ `backfill_verified_batch`
>   （扫 RESOLVED/CLOSED 工单 → 结案后新讨论 → 回填 verified；幂等、不阻断、找不到跳过）。
> - `ai/core/retrieval.py`：`RetrievalResult` 增加 `verified/root_cause_type/error_codes` 透出；`_rrf_fusion` 从 payload
>   读取；`retrieve_task_resolutions` 按 verified 调权（confirmed×1.15 / recurred×0.85 / rejected×0.7）；
>   新增 `update_task_resolution_verified`（按 task_id scroll+set_payload 回填）。
> - `retrieval_utils.py`：`format_retrieval_results` 对 task_resolutions 标注 `[已验证]`/`[已被推翻]`/`[该问题复发过]` + 根因类型。
> - 全部 py_compile 通过 + 一次性验证脚本通过（信号判定 + 标注），已删。

### 1.5 复发/趋势提示（扩展）

`retrieve_history` 返回时若发现**同 error_code 在近期高频出现**（Qdrant 按时间聚合），附加一句："同类问题 N 天内出现 M 次，建议提 issue/变更评估"。这需要 Qdrant 存 `created_at` 并按 `error_codes` + 时间聚合。

---

## 2. 优先级二：Ask-user 澄清闭环（追问落地到 discuss）

### 2.1 产品定位（响应用户的澄清）

- **`diagnose`（[帮我分析]）**：单向按钮，**不在其内插追问循环**（保持报告即时性）。
- **`discuss`（@AI）**：天然多轮对话，**追问的落点**。
- **衔接**：`diagnose` 识别"关键信息缺失 → 低置信"时，在报告里**主动吐出追问项**，前端引导用户到 `discuss` 继续，把回答喂回去。

### 2.2 关键信息查漏（diagnose 新增）

`diagnose` 在出报告前加一步 `_info_gap_detect(context, att_has_logs, att_log_summary, user_discussion)`，
对**高价值前提**做查漏：

| 关键信息 | 判定缺失 | 影响 |
|---------|---------|------|
| 故障发生时间 | collected_info 无 occurrence_time 且描述/日志/讨论无时间 | 大日志无法用时间窗定向，置信度低 |
| 是否可复现 / 复现频率 | collected_info 无 frequency 且全文未见 | 影响"版本缺陷 vs 偶发竞态"判定 |
| 变更了什么 | 描述/讨论未见版本/配置/变更 | 版本/配置相关根因无法定向 |
| 报错现场 / 现场动作 | 描述/讨论未见现象/步骤/操作 | 无法关联操作-故障 |

- **核心原则（用户强调 2026-08-16）**：提单 Agent 在 dialog 里**通常已收齐**这些信息
  （`collected_info.occurrence_time` / `frequency`，且 description 会总结"调度版本/发生时间"等）。
  `_info_gap_detect` **以 collected_info 结构化键为准 + 描述/日志/讨论兜底**，只对**确实缺失**的项查漏——
  **正常提单场景 `missing_info` 应为空**，绝不重复追问用户已给的信息。
- 查漏结果并入报告字段：`missing_info: [{key, question, why}]`；`needs_more_info` 由 `missing_info` 驱动。
- 报告 prompt 新增 `{missing_info}` 区块：**排查优先 + 一次性建议**——报告主体照常生成，
  只有确因缺关键信息才在末尾用一两句【建议】带出，不打断，不罗列。

> **✅ P3 已实现（2026-08-16）**：`_info_gap_detect`（collected_info 键感知 occurrence_time/frequency +
> 描述/日志/讨论兜底，最多 3 条，正常提单为空）+ DIAGNOSE_USER_TEMPLATE 增 `{missing_info}` 区块
> （排查优先，缺才建议）+ diagnose 返回 `missing_info`/`needs_more_info`。
> 验证脚本确认：提单已收时间/频率→不重复建议、信息齐全→空、空工单→≤3条、有日志无时间→建议补时间。
> 全量 py_compile 通过。

### 2.3 收敛 `ask_user` 到真闭环（discuss 修复核心债）

现状 `ask_user` 是 dead code（只解析不消费）。改造：

1. **Supervisor 消费 `ask_user`**：`SupervisorDecision` 增加 `questions: list[str]`；
   调度 LLM 在 `ask_user=true` 时列出**需要向用户确认的具体问题**；`run()` 把 `questions`
   透传回调用方（discuss_flow）。
2. **`discuss_flow`「排查优先 + 一次性建议补充」（已实现）**：
   - 不单独走澄清 prompt，一律先生成**分析答复**（DISCUSS 正常路径，先给方向/推断）；
   - 若 `ask_user=true` 且有 `questions`，把待确认项作为**可选的"结尾建议补充"**追加到答复末尾；
   - **措辞是"建议"，不是"提问"**——"如果有以下信息，定位会更准"，不审问、不索要；
   - **只建议一次**：追加时写入稳定标记 `🔄 U老师已建议补充`（`CLARIFY_SUGGEST_MARKER`）；下一轮检测到该标记
     （扫未截断的原始评论）即视为"已建议过"，本轮不再重复建议，转而基于现有信息
     继续给结论/方向——用户没提供也不会反复催；
   - **追加放在 Evaluator 之后**，避免自评/重写把建议尾巴改掉；
   - 已覆盖的问题自动跳过。
3. **`needs_more_info` 从"机械 flag"升级**：由 `_info_gap_detect` 生成 `missing_info` 列表驱动（而非仅 `conf<0.5`）。

### 2.4 前端衔接（最小改动）

- 诊断报告 `Dialog` 的「🤔 我还需要确认」区块：每项是 `@AI 追问` 短链（复用什么诊断短链接交互），点击 → discuss 带问题。
- 不需要新增后端端点（复用 `/discuss`）。

---

## 3. 优先级三：车型级经验记忆 + 知识资产 + 度量

### 3.1 车型级经验库（domain_experience 能力）

把「车型 × 错误码簇 → 历史根因 + 解法 + 验证状态」做成本地结构化索引，供 Supervisor 主动调度：

```python
# capabilities/tools/domain_experience.py
class DomainExperienceCapability(BaseCapability):
    name = "domain_experience"
    description = "车型级历史排查经验：按车型+错误码簇查历史根因/解法/验证状态，识别已知坑与复发信号"
    tags = ["experience", "经验", "车型", "复发"]
```

- 数据来源：**复用 3.1 的结构化 `ResolvedRootCause`**（Qdrant 或本地聚合缓存），按 `robot_type` 分组。
- 命中逻辑：`robot_type ∩ error_codes` 精确 → 相似降级 → 无则返回"该车型暂无可复用经验"。
- 价值：排查 Agent 首次就能带着"这个车型以前哪几个坑 + 哪个已验证 + 哪个被推翻"上阵，而不是从零开始。**这与"老工程师"的排查方式对齐。**

### 3.2 产品知识资产标准化（隐形瓶颈）

- 手册骨架统一：`现象 → 判别条件 → 根因 → 解法 → 验证要点`。
- 与 3.1 的验证回填联动：手册里已沉淀"经验证根因"要能被 `domain_experience` 检索。
- 车端/服务号手册当前还薄（D17 未做）——这次只定**骨架标准**，不铺全部产品。

### 3.3 质量评估集（度量能力有没有变强）

- 建一批**已结案、根因明确**的真实故障作为回归集。
- 每次能力升级，对这批 case 跑 `diagnose`，统计：根因命中率 / 定位路径合理性 / 报告可用度（`missing_info` 是否收敛）。
- 让"提升 Agent 能力"从拍脑袋变**数据驱动**。

---

## 4. 落地顺序（Phase 拆解）

| Phase | 内容 | 依赖 |
|:---:|------|------|
| **P1** | 结构化根因 schema + `submit` 写入升级 + `verified` 字段 | 无（地基） |
| **P2** | 验证回填扫描 + 检索侧权重/标注（✅ 已实现已验证） | P1 |
| **P3** | `_info_gap_detect` 查漏 + 报告 `missing_info` 区块（✅ 已实现已验证） | P1 |
| **P4** | Supervisor 消费 `ask_user` + discuss「排查优先+一次性建议补充」（✅ 已实现已验证） | P3 |
| **P5** | `domain_experience` 能力 + 检索/调度接入 | P1/P2（数据源） |
| **P6** | 手册骨架标准 + 质量评估集 | P1-P5 |

> **每 Phase 独立可交付、可验证、可回退**，不一次性大改。

---

## 5. 风险与取舍

| 风险 | 缓解 |
|------|------|
| 追问打断报告即时性 | 追问放 `discuss`；`diagnose` 只"吐 missing_info"不阻塞、不插循环 |
| 结构化 submit 增加一次 LLM 调用 | temp=0 小调用；解析失败回退现有扁平文本（保持兼容） |
| 验证回填误判（把"自己绕过去"当 confirm） | 关键词 + LLM 双轨；只对结案工单；保留人工复核可绕过 |
| 车型经验库冷启动（数据少） | 先由历史结案工单回填；数据量不足时该能力返回"暂无可复用经验"，不降级主流程 |
| 改动牵动现有 /index_solution / retrieve | 先加字段兼容旧 payload；检索侧在旧数据上正常降级 |

---

## 6. 与既有的关系

- 不重构内核（Supervisor / 能力注册表 / Router 不动结构）。
- 复用量：`TASK_AGENT_TARGET_ARCH.md` 的 `runtime_ctx`、`BaseCapability.is_available()`、`CapabilityResult`、`summarize` worker、`TodoList` 生命周期、`tracing`。
- 复用已存在但未消费的字段：`ask_user`、`needs_more_info` —— **「追问闭环」本质是"把已建好的脚手架接通"，改动成本低、收益直接。**
