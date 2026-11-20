# 设计：AI 评测 P0 — 否决规则 + Bad Case 台账

> 日期：2026-08-12 | 状态：设计稿（待人工确认）
> 依据：`automation/docs/gap-analysis-ai-eval-skill.md` 第 4 节 P0 项
> 范围：`automation/` 内新增/修改 10 个文件以内，不触碰 `ai/` `backend/` `frontend/`

---

## 1. 目标

1. **否决规则**：golden 用例可声明"一票否决项"（如"不得编造检查项""不得给出确定性故障结论"），命中任一否决项 → 用例失败，**rubric 平均分不覆盖否决**；judge 输出不可解析 → 标记 `veto_pending`（待人工复核），同样不通过
2. **Bad Case 台账**：失败用例沉淀为长期台账（case_id + 失败模式 + 风险等级 + 复现输入），支持去重、计数、修复状态回填，形成"失败 → 台账 → 修复 → 复测"闭环

## 2. 涉及文件清单

| # | 文件 | 动作 | 说明 |
|---|------|------|------|
| 1 | `automation/src/ai_metrics/veto.py` | 新增 | 否决规则判定（judge 依赖注入，纯逻辑可单测） |
| 2 | `automation/src/ai_metrics/tests/test_veto.py` | 新增 | 否决指标自测（FakeJudge，仿 test_llm_judge.py 风格） |
| 3 | `automation/src/ai_metrics/__init__.py` | 修改 | 导出 veto 模块 |
| 4 | `automation/tests/ai/runner.py` | 修改 | `EvalResult.veto_pending`；`_eval_l3` 接入否决；失败时附 Allure"台账建议" |
| 5 | `automation/testdata/fixtures/ai/bad_case_log.json` | 新增 | 台账（空表 + schema 说明） |
| 6 | `automation/scripts/cli-update-bad-cases.py` | 新增 | 合并失败记录入台账（--input/--dry-run） |
| 7 | `automation/testdata/fixtures/ai/diagnosis.json` | 修改 | DIAG-001/002 增加 `expect.l3.veto_rules`（示范 + 回归） |
| 8 | `automation/docs/AI_TESTING.md` | 修改 | 增补否决规则 + 台账章节 |
| 9 | `automation/docs/worklog/task-21-ai-eval-p0-veto-badcase.md` | 新增 | 任务记录（实现阶段写） |

**不改**：`llm_judge.py`/`faithfulness.py`（复用其 judge 注入模式）、Mock 后端、CI 脚本。

## 3. 模块职责划分

### 3.1 `veto.py`（指标层，L3 扩展）

```
check_veto_rules(question, answer, rules, judge) -> {
  "vetoed": bool,          # True: 命中任一否决规则
  "violated": [int],       # 命中规则的索引（原文规则）
  "reason": str,           # judge 理由
  "uncertain": bool        # judge 输出不可解析 → True（fail-safe 待复核）
}
build_veto_prompt(question, answer, rules) -> str   # 纯函数，可单测
```

- judge 协议：复用 `LLMJudgeClient.complete`（与 faithfulness 相同注入方式）
- 判定：`violated` 非空 → `vetoed=True`；judge 输出 JSON 不可解析 → `uncertain=True`（不当作"无违规"）
- **与 rubric 的关系**：独立判定。rubric score ≥ min 且 veto 命中 → 仍判失败（平均分不覆盖否决）

### 3.2 `runner.py` 改动

- `EvalResult` 新增字段 `veto_pending: bool = False`
- `_eval_l3`：`expect.l3` 出现 `veto_rules` 时，rubric 打分后再跑否决判定：
  - judge 不可用 → 按现有约定 append `skipped`（不失败）
  - vetoed 或 uncertain → `veto_pending=True`，check `{layer: l3, metric: veto, passed: False}`
- `run_ai_case`/`run_analysis_case`：`passed=False` 时附 Allure JSON 附件 `bad-case-suggestion`（case_id / 失败 checks / 复现输入 / 建议风险级），供人工一键进台账
- **pytest 运行中不写台账文件**（testdata 只读），台账由 CLI 合并

### 3.3 `bad_case_log.json` schema

```json
{
  "description": "AI 评测 Bad Case 台账（长期沉淀，非单轮报告）",
  "log": [
    {
      "case_id": "DIAG-001",
      "failure_mode": "veto|l1|l2|l3",
      "risk_level": "P0|P1",
      "repro_input": "复现输入（turns 摘要）",
      "first_seen": "2026-08-12",
      "last_seen": "2026-08-12",
      "count": 1,
      "status": "open|fixed|wonfix",
      "note": ""
    }
  ]
}
```

- 录入原则（对齐 skill）：只记 P0/P1、反复出现、代表性失败；`veto`/事实错误 → P0，其余 → P1

### 3.4 `cli-update-bad-cases.py`

```
usage: cli-update-bad-cases.py --input <failures.json> [--dry-run]
```

- `--input`：失败记录数组 `[{case_id, failure_mode, repro_input, risk_level}]`（来源：Allure 附件或手写）
- 行为：读台账 → 按 `case_id+failure_mode` 去重 → 命中则 `count+1`/更新 `last_seen`，未命中则追加（默认 `status=open`）
- `--dry-run`：只打印合并计划不写文件（对齐 cli-merge-ai-cases.py 惯例）
- 自动备份 `.bak`（对齐现有 CLI 工具惯例）

## 4. 测试用例变更

### 4.1 新增自测 `test_veto.py`（FakeJudge 注入，≈8 条）

| 用例 | 场景 | 断言 |
|------|------|------|
| 1 | judge 判无违规 | `vetoed=False` |
| 2 | judge 判命中规则 1 | `vetoed=True` + `violated=[1]` |
| 3 | 多规则命中多个 | `violated=[1,3]` |
| 4 | judge 输出不可解析 | `uncertain=True` + `vetoed=True`（fail-safe） |
| 5 | rules 为空 | 直接返回无否决（不调 judge） |
| 6 | 回答为空字符串 | 正常走 judge 流程 |
| 7 | prompt 包含全部规则原文 | 断言 build_veto_prompt 内容 |
| 8 | 与 rubric 独立 | score 高但 veto 命中 → 用例仍失败（runner 层语义，自测断言 check_veto_rules 返回值） |

### 4.2 golden 数据变更

- `diagnosis.json`：DIAG-001 增加 `"veto_rules": ["不得编造具体故障结论", "不得跳过排查步骤"]`；DIAG-002 增加 `"veto_rules": ["用户说转工单时不得追问排查问题"]`
- 其余 suite 本期不加（数据驱动，无 veto_rules 即零影响）

### 4.3 验证命令

```powershell
cd automation; pytest src/ai_metrics/tests/ -v          # 新增自测全绿（Fast Lane）
cd automation; pytest tests/ai/ -v                       # 无服务环境优雅 skip，不回归
cd automation; python scripts/cli-update-bad-cases.py --input sample.json --dry-run
```

## 5. 实现步骤（顺序执行，每步验证）

| 步骤 | 内容 | 验收 |
|------|------|------|
| 1 | `veto.py` + `test_veto.py` + `__init__.py` 导出 | 8 条自测通过 |
| 2 | `runner.py`：veto_pending + _eval_l3 接入 + bad-case 附件 | 自测不回归；tests/ai 收集正常 |
| 3 | `bad_case_log.json` + `cli-update-bad-cases.py` | --dry-run 合并计划正确；.bak 备份生效 |
| 4 | `diagnosis.json` 加 veto_rules；`AI_TESTING.md` 增补 | JSON 可加载，用例数不变 |
| 5 | worklog + 全量回归 | 全部通过 |

## 6. 风险分析

| 风险 | 等级 | 缓解 |
|------|------|------|
| veto 每用例多 1 次 judge 调用 | 中 | 仅 `veto_rules` 存在的用例（当前 2 条示范），L3 本就 `-m judge` 可选 |
| judge 不可解析被误判 | 中 | `uncertain` fail-safe，detail 说明原因；不计入 `violated` 具体规则 |
| 台账文件被误提交/膨胀 | 低 | 只有 CLI 手动写入；只有代表性失败才录入；`.bak` 备份 |
| 新增字段破坏旧数据 | 低 | `veto_rules` 为可选字段，缺失时行为与现状完全一致 |
| 多轮用例 veto 判定的回答主体 | 低 | 与 rubric 一致取最后一轮响应（现有语义） |

## 7. 待确认决策

| # | 问题 | 建议 |
|---|------|------|
| 1 | 否决命中时是否同时输出"建议风险级"到 Allure | ✅ 附件带 risk_level=P0（veto）/P1（其余失败） |
| 2 | 台账是否本期接入 CI 自动生成 | 否，先手动 CLI（CI 依赖真实 AI 服务，本期不引入） |
