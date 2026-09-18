# 差距分析：ai-evaluation-skill 方法论 vs 现有 AI 评测体系

> 日期：2026-08-12 | 状态：分析稿（待人工确认）
> 对象：`YLTsing/ai-evaluation-skill`(MIT) 方法论 / 现有 `automation/src/ai_metrics/ + tests/ai/` 体系
> 依据：skill 的 README.md + SKILL.md(382 行) / `automation/docs/AI_TESTING.md` / worklog task-16

---

## 1. 背景与目标

视频《功能测试要凉了？带你全面认识 AI 测试》提出 AI 测试三大支柱：**大模型评测、Harness 工程、知识库建设**。项目已按此落地第一代 AI 评测体系（L1/L2/L3 三层金字塔）。

`YLTsing/ai-evaluation-skill` 是开源的 AI 产品评测 Skill，与项目体系同源同向，但多了三套项目目前没有的机制：**否决规则 + 人工复核门禁、Bad Case 台账闭环、评测运行版本化**。本分析对比两者，确定哪些方法论值得借鉴进 `automation/`，哪些不适合。

## 2. 双方现状速览

### 2.1 项目现有（AI_TESTING.md 已交付）

| 能力 | 现状 |
|------|------|
| 评测分层 | L1 确定性断言 / L2 faithfulness+recall / L3 LLM-as-Judge |
| Golden 数据 | `testdata/fixtures/ai/*.json`：diagnosis 10 / assigner 6 / rag 8 / data_analysis 4 |
| 执行器 | `tests/ai/runner.py`：多轮驱动 + L1/L2/L3 编排 + 服务不可达优雅 skip |
| Judge | `ai_metrics/llm_judge.py`：DeepSeek rubric 打分 1-5 |
| 报告 | Allure（用例级 request/response/score 附件） |
| 门禁 | 无人工复核；L3 默认不跑（`-m judge`） |

### 2.2 skill 提供（仅方法论层面）

| 机制 | 说明 |
|------|------|
| 产品事实确认门禁 | 生成资产前先确认产品事实（服务对象/输入输出/版本/入口），未知项提问，不按假设生成 |
| 输入保真契约 | 逐输入槽位定义覆盖层级（模型层/API 层/端到端），素材包含 manifest 冻结 |
| Benchmark 用例字段 | 含**典型失败模式、风险等级、一票否决规则、人工复核触发条件**（12 字段 CSV） |
| Rubric + 否决分离 | 平均分不得覆盖事实错误/安全/合规等否决项 |
| Judge 协议 | 结构化 JSON（逐维度分数+证据+否决候选+不确定性），隐藏候选身份，金标校准 |
| **人工复核门禁** | 强制复核（1 分/不确定/否决候选/分差≥1）+ 抽样 10%；完成率 100% 才出正式报告 |
| **Bad Case 台账闭环** | `07_bad_case_log.csv` 长期台账（仅 P0/P1/反复出现）→ 归因修复 → 复测 → 回流 Benchmark |
| 运行版本化 | `runs/{run-id}/`：run.json 元数据 + results.csv 长表（case×run 一行），支持版本对比、断点续跑 |
| 持续评测机制 | 模型/Prompt/检索/工具变更触发 + 定期触发 |
| 数据门禁 | 无真实数据不得虚构评分/结论；报告门禁不满足只出初步分析 |
| A/B 专项 | 离线准入 + 在线分流方案，无真实数据只出设计 |

---

## 3. 逐项对照

| # | skill 机制 | 项目现状 | 结论 |
|---|-----------|---------|------|
| 1 | 评测分层（准确/相关/任务完成度） | L1/L2/L3 金字塔 | ✅ 已覆盖，不重复建设 |
| 2 | Rubric 打分（1-5，分档证据） | rubric 打分 1-5（`llm_judge.py`） | ✅ 已覆盖，分档证据可增强 |
| 3 | LLM-as-Judge | DeepSeek judge（`llm_judge.py`） | ✅ 已覆盖 |
| 4 | **一票否决规则与平均分隔离** | 无否决概念，L1 失败即用例失败 | ⚠️ 部分覆盖 → 借鉴 |
| 5 | **人工复核门禁（强制+抽样）** | 无人工环节，`-m judge` 结果直接入库 | ❌ 缺口 → 借鉴 |
| 6 | **Bad Case 台账 + 回流闭环** | 失败即报告，无台账沉淀/回归 | ❌ 缺口 → 借鉴 |
| 7 | **运行版本化（run-id/results.csv/断点续跑/版本对比）** | 一次性执行，Allure 附件不留结构化运行表 | ❌ 缺口 → 借鉴 |
| 8 | **持续评测触发（变更/定期）** | 手动跑 `pytest tests/ai/`，无触发机制 | ❌ 缺口 → 借鉴 |
| 9 | 产品事实确认门禁 | golden 数据基于接口契约+文档构造（task-16 记录），来源未版本关联 | ⚠️ 部分 → 轻量借鉴 |
| 10 | 输入保真契约/素材包（PDF 等二进制） | 纯文本输入，无素材包 | ⚠️ 当前产品（微信文本咨询）不涉及 → 不适用 |
| 11 | Judge 校准/金标/波动检查 | 无 | ⚠️ 借鉴（低成本） |
| 12 | 候选身份隐藏/角色隔离 | judge 直接打分，无隔离 | ⚠️ 低优先 |
| 13 | A/B 实验分支 | 无 | ❌ 当前阶段不做 |
| 14 | command/import 适配器执行 | HTTP（ai_client）+ 直接 import（rag/assigner） | ✅ 已覆盖对应场景 |
| 15 | 模板一致性强制 + validate_outputs | 无模板概念（JSON fixture） | ⚠️ 不适用，项目用 Allure 报告 |

---

## 4. 建议借鉴项（按优先级）

### P0 — 质检闭环（直接影响"评测可信度"）

1. **否决规则与平均分隔离**
   - 位置：`automation/src/ai_metrics/` 新增 `veto.py`（或并入 `llm_judge.py`）
   - 内容：golden 用例 `expect.l3` 增加 `veto_rules`（如"不得编造检查项""不得给出确定性故障结论"），命中任一否决项 → 整条用例标记 `veto_pending`，**平均分不覆盖否决**
   - 验收：指标自测 + 全量回归不破坏现有 42 条自测

2. **Bad Case 台账**
   - 位置：`automation/testdata/fixtures/ai/bad_case_log.json`（替代 skill 的 CSV，保持项目 JSON 风格）
   - 字段：case_id、失败模式、风险等级（P0/P1）、复现输入、首次日期、修复状态
   - 闭环：失败用例 → 录入台账 → 修复后复测 → 状态回填；台账与 golden 分离（golden 只放期望通过的）
   - 触发：`tests/ai/` 失败时自动附台账建议（Allure 附件）

### P1 — 运行版本化与持续评测

3. **评测运行记录**
   - 位置：`automation/output/ai-eval-runs/{run-id}/`（gitignored）：`run.json`（版本/时间/环境/通过率）+ `results.csv`（case×run 长表，结构化分数）
   - 改动：`tests/ai/runner.py` 增加 run 记录输出（复用现有 L1/L2/L3 结果对象，不重写评估逻辑）
   - 价值：版本对比（prompt/知识库变更前后跑两次 → 趋势可量化）

4. **持续评测触发**
   - 位置：`automation/ci/scripts/` 新增 ai-eval 脚本 + `.github/workflows/ai-test.yml` 扩展（或独立 `ai-eval.yml`，定时 + 变更触发）
   - 与现有 CI 差异：现有 `ai-test.yml` 是"AI 生成用例"，本项是"AI 评测执行"，需分开命名
   - 注意：依赖真实 AI 服务 8401，CI 环境需起服务，否则 skip —— 先做"服务可用才跑"

### P2 — Judge 增强（低成本高收益）

5. **人工复核门禁（轻量版）**
   - 内容：`-m judge` 运行时把"否决候选 + 不确定 + 1 分项"汇总输出 `human_review.csv`（人工确认后回填），未复核不出正式结论
   - 轻量化：当前阶段只做"候选清单输出"，不强制 100% 门禁（避免阻塞本地使用）
6. **Judge 校准**：金标 5-10 条（人工标注）→ 定期对照 judge 一致性；固定输入重复打分查波动
7. **否决候选身份隐藏**：judge 请求不回传预期答案（当前已基本满足，补充说明即可）

---

## 5. 不建议借鉴项

| 项 | 原因 |
|----|------|
| A/B 实验分支 | 当前无在线分流场景，属过度设计 |
| 二进制素材包/manifest | 产品输入是微信文本咨询，无 PDF/图片解析场景 |
| 模板一致性强制（Markdown 章节保序） | 项目资产是 JSON fixture + pytest + Allure，无 Markdown 资产模板 |
| command 适配器/断点续跑 | 现有 HTTP/import 已覆盖；断点续跑对 pytest 场景收益低 |
| Judge 独立服务化 | skill 明说依赖宿主 Agent，与项目"确定性回归"定位冲突 |

---

## 6. 落地方式建议

**方案 A（推荐）**：只借鉴方法论，全部落地在 `automation/`（符合 AGENTS.md 边界，不触碰 `ai/`）
- 新增/改动文件预估：`veto.py`、`bad_case_log.json`、`runner.py`（run 记录）、`ci/scripts/ai-eval.py`、`docs/AI_TESTING.md`（增补章节）
- 规模 < 10 文件，可分 3 轮实现（P0→P1→P2），每轮走"设计→确认→实现→测试→worklog"

**方案 B**：同时把 skill 本体安装到 opencode（`.agents/skills/` 或全局 skills 目录），让 AI 用它设计评测资产
- 与方案 A 不冲突，但 skill 面向 Codex 系 Agent，调用方式（npx skills add）需适配；建议先 A 后 B

## 7. 风险分析

| 风险 | 等级 | 缓解 |
|------|------|------|
| 人工复核门禁拖慢本地使用 | 中 | P2 轻量化：先出清单不强制门禁 |
| Bad Case 台账变摆设 | 中 | 只有 P0/P1/反复失败才录入，与 worklog 关联 |
| run 记录增加执行时间/存储 | 低 | 输出目录 gitignored，CSV 长表小体积 |
| CI 无 AI 服务导致空跑 | 中 | 服务不可达自动 skip（现有机制），报告注明 |
| 与 skill 版权/实现偏差 | 低 | 只借鉴方法论（MIT），实现完全自研 |

## 8. 待确认决策

| # | 问题 | 建议 |
|---|------|------|
| 1 | 先落地 P0（否决+台账）一轮,还是 P0+P1 一起 | 一轮一模块：先 P0 |
| 2 | 人工复核门禁是否本期做 | 建议 P2 轻量版 |
| 3 | 是否需要安装 skill 本体（方案 B） | 可选，建议 A 跑通后再定 |
