# AI 测试流水线设计：代码推送 → 自动生成测试 → Allure 报告

> 状态：设计稿（待人工确认） | 作者：automation 测试架构 | 日期：2026-08-06

---

## 1. 目标与范围

### 1.1 目标

业务代码推送至 GitHub 后，无需人工编写任何测试，自动完成：

```
代码 push / PR
   │
   ▼
① 读取代码 → 提取接口规格（OpenAPI）
   │
   ▼
② AI 需求分析（功能点/业务流/边界/风险）
   │
   ▼
③ AI 生成测试用例（正/异常/边界/权限）
   │
   ▼
④ AI 生成 pytest 脚本（自动校验可编译、接口在规格内）
   │
   ▼
⑤ 自动执行 pytest → 收集 Allure 结果
   │
   ▼
⑥ 生成 Allure 报告（GitHub Pages）+ PR 评论摘要
```

### 1.2 范围

- 覆盖 FastAPI 类后端服务的 HTTP 接口级测试（复用现有 `automation/` 平台能力）
- AI 产出（分析/用例/脚本）经**自动质量门禁**校验，不通过则循环修复（最多 2 轮）
- **不修改**业务代码；测试产物集中放在业务仓 `test-gen/` 目录或平台侧

### 1.3 非目标（本期不做）

- UI/E2E 自动生成（P3 规划内，本期不涉及）
- 生成脚本的人工审阅界面（用 PR Review 代替）

---

## 2. 接入模式（二选一）

### 模式 A：业务仓库内嵌（推荐起步）

在业务代码仓库放置 `.github/workflows/ai-test.yml`，push 直接触发。测试资产通过 git submodule 或 CI 内 `git clone` 拉取。

```
业务仓库/
├── .github/workflows/ai-test.yml   # 流水线
└── 业务代码...
```

- 优点：零平台建设，push 即测
- 缺点：每个业务仓都要装一份流水线；AI 生成产物提交回业务仓需要权限

### 模式 B：平台侧统一收件（平台化）

平台服务监听 GitHub Webhook（或 GitHub App），收到 push 事件后拉代码到平台 runner，分析生成测试并执行，报告回传。

- 优点：多业务仓统一管理、提示词/模板集中维护、权限收敛在平台
- 缺点：需要部署平台服务（GitHub App + 队列 + 存储）

**本期建议**：先做模式 A（一个 workflow 文件 + 共享脚本仓库），跑通后沉淀为模式 B。

---

## 3. AI 角色编排设计

采用四角色分层编排，所有 AI 调用走统一 LLM 客户端（DeepSeek，`DEEPSEEK_API_KEY`），无人工参与时由门禁把关。

| 角色 | 职责 | 输入 → 输出 | 质量校验 |
|------|------|------------|---------|
| `analyzer` | 代码/需求分析 | 接口规格 + 变更 diff + 领域文档 → `analysis.md`（需求概述/测试范围/重点/策略） | 结构校验（固定标题、非空） |
| `case-gen` | 测试用例生成 | analysis.md → `cases.json`（TC 编号、前置/步骤/预期、正/异常/边界标注） | 编号唯一、字段完整、用例数 ≥ 阈值 |
| `script-gen` | 脚本生成 | cases.json + OpenAPI → `test_*.py`（pytest + requests） | 编译通过、`pytest --collect-only` 成功、**接口路径全部存在于 OpenAPI**、不允许读取规格文件 |
| `gate` | 独立审阅（LLM-as-judge） | 各角色产出 + 校验规则 → 通过/修复建议 | 连续 2 轮不通过 → 标记"需人工介入"并继续执行已通过部分 |

**流程约束**（写入各角色提示词，防止越权）：

- 分析 → 用例 → 脚本 严格顺序，前一角色产出未过门禁不得进入下一环节
- 脚本生成只允许使用 OpenAPI 中声明的接口与字段，禁止编造
- 门禁角色与生产角色互相独立，不共享上下文历史

---

## 4. 关键设计决策

| 决策点 | 方案 | 原因 |
|--------|------|------|
| 接口提取 | 启动业务服务后取 `/openapi.json`（uvicorn + curl），失败则直接调 `app.openapi()` | 规格准确，FastAPI 原生能力 |
| 分析输入 | 接口规格 + 本次 push 的文件 diff + README/领域文档（存在时） | 定位变更影响面，减少 token 消耗 |
| 用例载体 | `test-gen/cases/{run_id}/cases.json`（不用 Excel） | CI 并发无写冲突；后续可一键转 Excel |
| 脚本载体 | `test-gen/tests/{run_id}/test_{module}.py`，直接 pytest 执行 | 与现有 Allure 流程无缝衔接 |
| 执行方式 | 复用现有 job 模式：MySQL/Redis/Qdrant services + `pip install -e automation` | 与 `test.yml` 一致，测试环境统一 |
| 报告 | 沿用现有 `report` job（`allure-report-action` + GitHub Pages） | 零新增，只加一个 artifact 来源 |
| 无人值守兜底 | 门禁连续失败 → 该环节降级为"跳过+PR 评论说明"，不阻断主测试 | push 流水线不能被 AI 失败卡死 |
| token 成本 | 脚本生成全量、分析采样（diff 超 2000 行只取摘要） | 控制预算 |

---

## 5. 新增文件清单

### 5.1 共享资产仓库（或 automation/ 下新增 `ci_ai_gen/` 模块）

```
automation/ci_ai_gen/
├── prompts/                     # 各角色系统提示词（与代码分离，可热改）
│   ├── analyzer.md
│   ├── case_gen.md
│   ├── script_gen.md
│   └── gate.md
├── extract_api.py               # ① 启动服务 → openapi.json（含失败兜底）
├── run_analyzer.py              # ② 分析 → analysis.md
├── run_case_gen.py              # ③ 用例 → cases.json
├── run_script_gen.py            # ④ 脚本 → test_*.py
├── run_gate.py                  # 门禁校验（结构校验 + LLM 审阅 + 编译/收集校验）
├── summarize_pr.py              # ⑥ 生成 PR 评论摘要
└── run_pipeline.py              # 全流程编排（步骤状态记录 run_id）
```

### 5.2 业务仓库侧（模式 A）

```
.github/workflows/ai-test.yml    # 流水线（见 §6）
test-gen/                        # AI 产物（gitignore，报告后清理或归档）
```

### 5.3 复用（零改动）

- 现有 `.github/workflows/test.yml` 的 `report` job、services 配置
- `automation/` 的 pytest/Allure/断言基础设施

---

## 6. GitHub Actions 设计（ai-test.yml 摘要）

```yaml
name: AI Test Generation

on:
  push:
    branches: [hxg, develop]
    paths:
      - 'backend/**'        # 仅业务代码变更触发，避免噪音
  pull_request:
    branches: [develop]

jobs:
  ai-testgen:
    runs-on: ubuntu-latest
    services:  # 与 test.yml 一致：mysql / redis / qdrant
    env:
      DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}   # 仓库 Secret
      RUN_ID: ${{ github.sha }}-${{ github.run_number }}
    steps:
      - checkout / setup-python / pip install
      - name: ① Extract OpenAPI
        run: python automation/ci_ai_gen/extract_api.py --out test-gen/spec/openapi.json
      - name: ②-④ AI analyze + generate (cases/scripts, gate 循环)
        run: python automation/ci_ai_gen/run_pipeline.py --spec test-gen/spec/openapi.json --run-id ${{ env.RUN_ID }}
      - name: ⑤ Execute generated tests
        run: pytest test-gen/tests/${{ env.RUN_ID }} -v --alluredir=automation/output/allure-results --junitxml=automation/output/junit-ai-gen.xml
      - name: Upload Allure results
        if: always()
        uses: actions/upload-artifact@v4
        with: { name: allure-results-ai-gen, path: automation/output/allure-results }
      - name: ⑥ Comment PR summary
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with: { script: 读取 test-gen/report/summary.md 后 createComment }

  # report job 追加 needs: [test-infra, test-api, test-auth, ai-testgen]
  # download-artifact pattern 加 allure-results-ai-gen，其余复用现有逻辑
```

---

## 7. 提示词设计要点（每个角色核心约束）

| 角色 | 必须 | 禁止 |
|------|------|------|
| analyzer | 输出固定一级标题（需求概述/测试范围/测试重点/测试策略）；基于实际代码，标注不确定项 | 编造规格中不存在的功能 |
| case-gen | 用例含 TC 编号 + 前置/步骤/预期；标注类型（正/异常/边界/权限）；覆盖每个接口三类场景 | 生成规格外字段的用例 |
| script-gen | 仅用 OpenAPI 中接口；requests + `verify=False`；断言状态码与响应 `code` 字段；中文注释；每个用例独立可跑 | 脚本内读取规格文件；修改业务代码 |
| gate | 按规则逐项审阅，输出结构化"通过/问题+修复建议" | 自行改代码；对不确定项放过 |

---

## 8. 实施步骤

| 阶段 | 内容 | 验收 |
|------|------|------|
| P0（本周） | `ci_ai_gen/` 骨架：extract_api + 提示词文件 + run_pipeline 编排（门禁只做结构校验，LLM 审阅后置） | 本仓 push 能跑通"提取规格→生成用例→生成脚本→pytest 执行" |
| P1（下周） | gate 接入 LLM 审阅 + 修复循环；PR 评论摘要；allure artifact 并入 report job | 生成用例 ≥ 8 条/接口集，脚本收集成功，报告出图 |
| P2（待评估） | 模式 B 平台化（webhook 收件）；用例/脚本人工反馈回流（PR review 评论 → 提示词迭代）；token 用量看板 | 多业务仓接入；生成质量趋势可观测 |

---

## 9. 安全与成本

- LLM 密钥：只存 GitHub repository secrets，不落盘、不打日志
- 执行环境：生成脚本在隔离 runner 执行，`verify=False` 仅限测试环境
- token 预算：每次 push 约 30-80K token（视接口量）；diff > 2000 行自动摘要
- 产物清理：`test-gen/` 加入 gitignore；报告保留最近 20 次（沿用 `keep_reports: 20`）

---

## 10. 风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| AI 生成脚本质量不稳定 | 高 | 门禁硬校验（编译/收集/接口存在性）+ 独立审阅角色 + 2 轮修复上限 |
| 流水线被 AI 失败阻塞 | 中 | 连续失败降级跳过并 PR 说明，不阻断主测试 |
| 接口规格提取失败 | 中 | 兜底 `app.openapi()` 直读；仍失败则跳过 AI 环节并告警 |
| token 成本失控 | 中 | 分析采样、提示词收敛、失败即停（不无限重试） |
| 生成用例漂移（接口变更后旧用例误导） | 中 | 每次 push 全量重新生成，run_id 隔离，不增量复用 |
| 无人值守误报 | 中 | 仅 PR 场景发评论；push 场景只出报告不告警 |

---

## 11. 待确认问题

1. 业务代码仓库范围：仅本仓 `backend/`，还是包括外部业务仓库（决定模式 A/B）？
2. 生成脚本执行环境：需要真实 MySQL/Redis/Qdrant services 还是 mock（影响成本与稳定性）？
3. 是否接受"生成质量门禁失败时自动跳过并出报告"的无人值守策略，还是首期强制人工确认后才执行？

## 12. 确认决策（2026-08-06）

| # | 问题 | 决策 |
|---|------|------|
| 1 | 触发范围 | ✅ 仅本仓 `backend/`（模式 A），后续再评估平台化 |
| 2 | 执行环境 | ✅ 真实 services（MySQL/Redis/Qdrant，复用 test.yml 配置） |
| 3 | 门禁失败策略 | ✅ 自动跳过 + 报告说明，不阻断 push |

## 13. 实现记录（P0 已交付 2026-08-06）

已交付：

```
automation/ci_ai_gen/
├── prompts/            # analyzer / case_gen / script_gen / gate 四角色提示词
├── extract_api.py      # 接口提取：HTTP 拉取或直读 app.openapi() 兜底；--prd 输入
├── gates.py            # 结构门禁（纯函数）：分析标题/用例 JSON/脚本路径/REQ 覆盖度
├── run_pipeline.py     # 编排：analyze → cases → script → gate(2轮修复) + summary.md
└── tests/              # 24 条测试（fake LLM，验证顺序/门禁/修复循环/降级/PRD 模式）
.github/workflows/ai-test.yml   # push/PR 触发，复用现有 services + Allure
```

验证：`ci_ai_gen/tests/` 24 passed；全量回归 276 passed, 28 skipped。

### PRD 驱动模式（初版，2026-08-06 新增）

流水线支持两种驱动模式，`extract_api.py --prd <路径>` 或 `spec_dir/prd.md` 存在时启用：

| 阶段 | 接口驱动（后期目标） | PRD 驱动（当前初版） |
|------|---------------------|---------------------|
| 需求来源 | OpenAPI 接口清单 | PRD 文档（`references/prd/`） |
| 分析产物 | 四标题结构 | 五标题：需求概述/功能点清单(REQ-xx)/状态流转/权限矩阵/测试策略 |
| 用例生成 | 按接口覆盖 4 类 | 按功能点生成，`req_id` 关联 REQ 编号，含 flow（状态流转）/auth（权限矩阵） |
| 覆盖度门禁 | 接口 × 用例 | **REQ 功能点 × 用例映射**（缺失即不通过） |
| 脚本生成 | 相同 | 相同（仍需 OpenAPI 保证可执行） |

计划：初版跑稳后，把需求来源切回接口驱动，机制完全复用（切换仅改输入与门禁）。

### 用例归档（2026-08-06 新增）

流水线结束时自动归档到 `automation/references/generated-cases/{run_id}/`（可 `--archive-dir` 覆盖）：

```
references/generated-cases/{run_id}/
├── analysis.md      # 需求分析
├── cases.json       # AI 生成的用例（TC + req_id）
├── cases.xlsx       # ★ 已映射为平台 Excel 用例格式（与 testdata/cases 同列）
├── test_gen.py      # 生成的 pytest 脚本
└── summary.md       # 阶段摘要
```

- Excel 列与正式用例库完全一致：`id/module/method/path/auth/role/payload/expected_status/expected_fields/type/note`，状态码从预期结果中自动提取，payload 取自首个步骤 testData
- 归档目录已 gitignore：CI 产物不入库；**审阅确认后**再将 `cases.xlsx` 内容合并进 `testdata/cases/api-test-cases.xlsx` 转正（即"AI 生成 → 人工确认 → 正式用例库"闭环）
- 验证：`ci_ai_gen/tests/` 36 passed；全量回归 288 passed, 28 skipped

### 真实运行结果（2026-08-06，DeepSeek + 本仓 backend 161 接口 + 摇人吧 PRD）

demo-008 全链路真实跑通：

| 阶段 | 结果 |
|------|------|
| analyze | ✅ 88 个 REQ 功能点（五标题结构） |
| cases | ✅ **373 条用例**（positive 166 / negative 95 / auth 67 / edge 39 / flow 6），覆盖 72 个 REQ，已导出 cases.xlsx |
| script | ✅ 15 个 pytest 脚本文件（可编译、可收集、URL 均在 OpenAPI 内） |
| gate | ⚠️ 审阅有效但未过：抓出硬编码 ID、字段越界、状态码断言越界、用例顺序依赖、缺少认证头等问题（修复循环生效，部分文件已自修复） |

**过程中修复的工程问题**（均已入测试）：
1. LLM 输出超长截断 → 用例按 5 REQ/批分批生成合并、脚本按 25 用例/批分文件
2. 摘要不完整导致 gate 误报 → `$ref`/`allOf` 展开、请求体字段列表注入提示词
3. LLM 输出带 ``` 围栏/JSONC 注释 → 解析层剥离 + 注释清理
4. gate 输出超长截断 → 限制最多 10 条 issue、每条 ≤100 字

**已知待改进**（后续迭代）：
- REQ-73~88 个别批次生成失败（偶发 JSON 截断）→ 批次级失败重试
- 脚本中硬编码 ID（task_001 等）→ script 提示词加强"动态创建前置资源"
- gate 修复循环按文件串行（15 文件 × 多轮），成本较高 → 可并行或只审阅代表性文件
