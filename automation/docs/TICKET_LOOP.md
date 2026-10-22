# 需求工单自动化闭环

从「需求类工单新建」到「合并 test 分支后自动跑测试」的完整闭环。

## 闭环总览

```
① 摇人吧服务号出现需求类工单（task_type=feature, status=new）
        │  ticket-pipeline.yml（每小时定时 / 手动触发）
        ▼
② AI 结合 OpenAPI 规格生成用例 + 确定性渲染 pytest 脚本
        │  references/generated-cases/tickets/{id}/
        ▼
③ 【人工 review】改 manifest.json 的 review_status = "approved"
        │  人工执行 promote
        ▼
④ references/promoted-cases/{id}/  （含可执行 test_gen.py）
        │  人工提交到仓库
        ▼
⑤ 工单状态变为 resolved/closed 且代码合并到 test 分支
        │  ticket-gate.yml（push to test）
        ▼
⑥ 自动执行：本次需求的功能用例 + 回归用例
```

## ① 扫描条件

`SSHMySQLTicketSource` 通过 SSH 隧道只读生产库 `tasks` 表，过滤条件全部可配：

| 条件 | 默认值 | 环境变量 |
|---|---|---|
| 项目 | `Leo_test`（摇人吧服务号-测试） | `PROD_TICKET_PROJECT_ID` |
| 状态 | `new` | `PROD_TICKET_STATUSES`（逗号分隔） |
| 类型 | `feature,bug` | `PROD_TICKET_TASK_TYPES`（逗号分隔） |
| 标签 | **空（不过滤标签）** | `PROD_TICKET_TAG`（填值才启用标签门槛） |
| 排除来源 | `ai` | `PROD_TICKET_EXCLUDE_SOURCES`（逗号分隔） |

> 「需求类工单」= `task_type=feature`（`backend/app/models/task.py:45`）。
> 「新建成」= `status=new`（`backend/app/models/task.py:28`）。
> 「已解决完成」= `status=resolved` 或 `closed`。
> 「自动触发」= **任何**新建的 feature 工单都会进入生成流程，**不再要求人工打 `auto_case` 标签**；
> 但 `source=ai` 的自动提单会被排除，否则 AI 提单 → AI 生成 → 再提单会自激循环。

## ② 用例生成

生成器：`automation/ci_ai_gen/ticket_pipeline.py::TicketCaseGenerator`

两次 LLM 调用：

1. `ticket_analysis.md` — 工单 → 测试分析
2. `ticket_case_gen.md` — 工单 + 分析 + **接口清单** → 结构化用例

用例的 `requests` 数组携带真实的 `method` / `path` / `body` / `expected_status`，
`path` 必须逐字取自 OpenAPI 规格（`automation/ci_ai_gen/extract_api.py` 抽取）。

脚本由 `automation/src/ticket_pipeline/script_renderer.py` **确定性渲染**，
不使用 LLM 写代码，因此产物必然可编译、可被 pytest 收集。

渲染产物结构：

```
references/generated-cases/tickets/{id}/
├── analysis.md        测试分析
├── cases.json         结构化用例
├── cases.md           人工 review 用的中文清单
├── test_gen.py        可执行 pytest 脚本
└── manifest.json      review_status=pending_review
```

`manifest.json` 里的 `spec_issues` 列出用了规格外路径的用例，review 时需重点确认。

## ③ 人工 review（必经环节）

用例**不会**自动进入正式测试集。review 步骤：

1. 阅读 `cases.md` 与 `test_gen.py`，确认接口、断言、预期是否正确。
2. 修正 `cases.json` / `test_gen.py`（如有需要）。
3. 把 `manifest.json` 的 `review_status` 改为 `approved`。
4. 执行 promotion：

```powershell
python automation/scripts/cli-ticket-pipeline.py promote --ticket-id 837
```

产出 `references/promoted-cases/{id}/`，包含 `promotion.json`（记录审核时间与来源）。

5. 把 `references/promoted-cases/` 提交到仓库并合入 `test` 分支，
   门禁才能在 CI 中找到这些用例。

未通过 review 的用例无法被 promotion——`promote()` 会拒绝并说明原因。

## ④ 门禁：功能用例 + 回归用例

`ticket-gate.yml` 在 `push` 到 `test` 分支时触发：

1. 取**本次推送范围内的全部 commit message**（`github.event.before..github.sha`），
   不是只看最后一个 commit——一个 PR 含多个 commit 时工单号可能在前面的 commit 里。
2. 提取工单号（`ORS-837` / `工单:837` / `ticket_id: 837`；**不认裸 `#838`**，那是 GitHub issue）。
3. **校验工单状态**：只有 `resolved` / `closed` 的工单才允许跑功能用例；
   其它状态（`new` / `in_progress` / 查无此单）一律跳过功能用例，只跑回归。
   **状态读取失败时 fail-closed**：跳过功能用例并告警，绝不「查不到就照跑」。
4. 功能用例与回归用例**分成两组分别执行**：
   功能用例跑真实后端（`USE_MOCK=0`）+ 种子数据，回归用例跑 Mock 后端（`USE_MOCK=1`）。
5. **门禁强度**：功能用例失败 → 只通知不阻断（写 notification + `::warning::`，
   可选回写工单评论）；回归用例失败 → 阻断分支。

状态校验由 `automation/src/ticket_pipeline/gate.py::evaluate_ticket_statuses` 实现，
工单状态通过 SSH 隧道只读生产库获取。

## ⑤ 种子数据（功能用例跑真实后端的前提）

CI 里是一次性空库，生成用例中的「登录 → 调接口」必然失败。因此
`ticket-gate.yml` 在迁移之后执行：

```powershell
python backend/scripts/seed_automation_env.py
```

幂等写入：

- 项目 `Leo_test`（名称 `摇人吧服务号-测试`）
- 账号 `automation_bot`（`admin` 权限，`active`）

环境变量：`SEED_PROJECT_ID` / `SEED_PROJECT_NAME` / `SEED_BOT_USERNAME` / `SEED_BOT_PASSWORD`。
脚本**不打印密码**，CI 把同一组变量传给门禁的 `TEST_USERNAME` / `TEST_PASSWORD`。

## 本地运行

生成用例（需要 LLM 与生产库只读权限）：

```powershell
python automation/scripts/cli-ticket-pipeline.py run --limit 20 --statuses new --task-types feature
```

指定接口规格（推荐，产出可执行脚本）：

```powershell
python automation/ci_ai_gen/extract_api.py --out automation/references/spec --backend-path backend
python automation/scripts/cli-ticket-pipeline.py run --spec-dir automation/references/spec
```

只跑回归的门禁（本地调试）：

```powershell
python automation/scripts/cli-ticket-gate.py --text "feature（ORS-837）：xxx" --regression tests/tasks
```

带工单状态校验的门禁（需要生产库只读权限）：

```powershell
python automation/scripts/cli-ticket-gate.py --text "feature（ORS-837）：xxx" --require-resolved
```

## 开关与密钥

| 名称 | 类型 | 作用 |
|---|---|---|
| `TICKET_PIPELINE_ENABLED` | repo variable | 开启用例生成流水线 |
| `TICKET_GATE_ENABLED` | repo variable | 开启 test 分支门禁 |
| `PROD_TICKET_SSH_PRIVATE_KEY` | secret | 生产库 SSH 私钥 |
| `PROD_TICKET_SSH_HOST/PORT/USER` | secret | 生产库 SSH 入口 |
| `PROD_TICKET_DB_PASSWORD` | secret | 生产库只读账号密码 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | secret | 用例生成所用 LLM |
| `SEED_BOT_USERNAME` / `SEED_BOT_PASSWORD` | secret / variable | 门禁种子账号（CI 一次性库） |
| `TICKET_COMMENT_API_BASE_URL` | variable | 回写工单评论的工单系统地址（不配置则只输出告警） |
| `AUTOMATION_BOT_USERNAME` / `AUTOMATION_BOT_PASSWORD` | secret | 回写评论用的工单系统账号 |

## 幂等

- 同一工单已生成过候选用例时默认跳过（`--force` 可强制重新生成）。
- 工单在生成后被更新（`updated_at` 晚于 `generated_at`）时自动重新生成。
- 门禁只读取 `promoted-cases/`，不会修改任何内容。
