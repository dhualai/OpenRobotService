# CICD 架构与硬约束 —— AI 强制契约（提示词）

> **用法**：AI 在本仓处理任何 CI/CD、部署、回滚、发布相关任务时，必须先完整读取本文并逐条遵守。本文是「提示词」，即交给 AI 的强制上下文，不是参考资料。
> **口径基准**：`test` 分支 `.github/workflows/deploy-split.yml`。本文与工作流实际内容冲突时，**以文件为准**：停下核实，并在同一 PR 内更新本文（防止文档漂移）。
> **口径快照**：2026-11-26（含 PR #521 手动单工单验收）。
> **配套文档**：`design-ci-pipeline-consolidation.md`（流水线收敛设计）、`TICKET_GATE.md` / `TICKET_PIPELINE.md`（工单验收体系）、`task-manual-ticket-acceptance.md`（手动单工单验收操作）。

## 0. 角色声明

你（AI）是本仓 CI/CD 的维护者。这条流水线直接操作生产环境：一处误改可能造成生产中断、数据风险或凭据泄露。因此本文第 2 节每一条都是**硬约束**，不是建议；动手前逐条自检，不确定就先问，绝不暗猜。

## 1. 架构现状（先读文件，不要凭记忆假设）

### 1.1 总体形态：分离式构建部署

- 主文件：`.github/workflows/deploy-split.yml`（部署、门禁、验收、报告一体承载；原 `test.yml`、`ticket-gate.yml` 已并入，勿再引用）。
- 触发：`workflow_dispatch` 为唯一主入口；`push` 仅限 `chore-cicd-split-deploy-verify` 分支（fork 验证期遗留机制，勿动勿删）。
- 手动触发界面的 "Use workflow from" **只决定读取哪个分支的 workflow 定义**；实际部署哪份代码由 `plan` 解析的 `git_ref` 决定，两者解耦。
- 并发组：`split-deploy-{env}`（同环境严格串行）；`ticket-acceptance-{run_id}`（单工单验收独立）。`cancel-in-progress: false`，不得改为可取消。

### 1.2 Runner 分工（安全边界）

| 类别 | runner | 承载 job |
|---|---|---|
| 托管 runner | `ubuntu-latest` | plan、gate_backend、gate_frontend、quality-gate、real-smoke、ai-tests、ticket-acceptance、allure-report、build、upload-logs |
| 自托管 runner | `[self-hosted, ors-verify]` | **仅 deploy、rollback**（生产凭据只允许在这里解密） |

注意：`manual-ticket-status`（读生产工单状态，使用 `PROD_TICKET_*` 生产凭据）截至快照日仍在 `ubuntu-latest`，迁移 PR #525 进行中；该约定落地前，**任何新增「触碰生产凭据」的 job 一律直接放自托管 runner**。

### 1.3 源码与产物流转

- 自托管服务器连不上 github smart HTTP，**目标机不 checkout 源码**：build job 用 `deploy/deploy.py --build-artifacts` 产 `_artifacts`（含部署脚本、`schema_sync.py`、`notify_image.py` + 通知字体），经 artifact 中转，deploy job 用 `--from-artifacts` 消费。两侧共用同一份打包定义，不得分叉。
- 回滚场景没有 artifact，使用自托管 runner 上的脚本缓存 `~/ors-ci`（`SELF_HOSTED_DIR`）。
- 服务器 `/tmp` 不可写，统一暂存目录 `~/tmp`（`DEPLOY_REMOTE_TMP`）。
- 自托管 job **禁止引入 `uses:` Node action**（每次 run 都会重新下载，实测 download/upload-artifact 合计 100s+，被 skip 的步骤也照下）；fallback 用 inline curl，日志由尾段托管 job `upload-logs` 上传。

### 1.4 Job 链（职责与顺序）

```
plan（输入校验与解析，唯一事实源）
 ├─ gate_backend（pytest）─┐ 并行
 ├─ gate_frontend（vitest）┘        ┐
 ├─ quality-gate（自动化框架自测+API mock，与 build 并行）
 ├─ real-smoke / ai-tests           ├→ build 产 artifact → deploy（自托管）
 ├─ allure-report                   ┘
manual-ticket-status（仅 acceptance）→ ticket-acceptance（验收场景）
rollback（自托管，独立入口）
deploy 失败 → upload-logs 兜底收集日志
```

- deploy 以 gate 两步 outcome 做「测试门禁」标注（非阻塞，见 2.D）；quality-gate / real-smoke / ai-tests 为观测性非阻塞。
- `ticket-acceptance` 在部署模式下 `continue-on-error`（非阻塞），在 acceptance 模式下硬失败即红。

### 1.5 入口参数语义（由 plan 解析，改语义必须同步本文）

| 输入 | 取值 | 硬语义 |
|---|---|---|
| `environment` | test / prod | prod 需确认词 |
| `action` | deploy / rollback / acceptance | acceptance 不部署，只验单工单 |
| `components` | all / frontend / backend / ai | 决定构建与门禁范围 |
| `git_ref` | 留空 ⇒ **test→test、prod→dev** | push 事件用被推分支；三重白名单校验：禁含 `:`（跨仓库 ref）、字符集校验、必须是本仓库既有分支 |
| `confirm` | prod 必须精确填 `DEPLOY-PROD` | 不符直接拦在门禁之前 |
| `skip_gate` | 默认 false | 紧急跳过会在日志显著标注；push 事件受 `vars.SPLIT_DEPLOY_FAST` 控制 |
| `backup_id` | rollback 必填（可 `latest`） | 缺失直接报错 |
| `backup_keep` | 默认 10 | `vars.DEPLOY_BACKUP_KEEP` 可覆盖 |

健康检查地址（服务器本机 curl）：test=`9400/api/health, 9401/health, /t/app/`；prod=`8400/api/health, 8401/health, /p/app/`。**9401 是测试算法服务；9411 属于另一服务 controlled-ai-api，勿混用。**

### 1.6 周边工作流

- `test-gate.yml`：push 到 test 触发全量自动化（只测不部署）；承接部署链路移出的接口回归。
- `ci-guard.yml`、`db-tests.yml`、`ui-smoke.yml`、`ui-regression.yml`：辅助门禁与冒烟。
- `ai-review.yml`（ORS-1149）：AI 检视，workflow_run 模式，非阻塞。
- `.github/workflows-archive/`：历史工作流归档，勿引用勿恢复。

## 2. 硬约束（MUST / 禁止）

### A. 安全红线

1. **生产凭据只允许在自托管 runner 上解密使用**。托管 runner 会把私钥/密码解密到第三方 VM，并从动态公网 IP 连入生产，留下无法区分来源的异地登录记录；自托管私钥不出内网，生产侧可对固定出口 IP 做白名单。新增任何触碰生产凭据的 job/step，`runs-on` 必须是 `[self-hosted, ors-verify]`。
2. 禁止把服务器地址、账密、token、私钥**硬编码**进工作流、脚本或文档；一律走 secrets / vars。本文与所有文档同样遵守（凭据只在 secrets 与内部运维渠道）。
3. `confirm=DEPLOY-PROD` 生产二次确认机制不得移除或弱化。
4. `git_ref` 白名单校验（禁跨仓库 ref、字符集、既有分支检查）不得移除——它阻断「把 fork 上的任意代码当作待部署代码」。

### B. 架构红线

5. `deploy` / `rollback` 两个自托管 job 禁止引入 `uses:`（见 1.3 实测代价）。
6. 一切 checkout 以 `plan.outputs.git_ref` 为准（acceptance 场景例外用 `github.sha` 且 `persist-credentials: false`）；不得擅自改 ref 口径。
7. 健康检查窗口 `40 × 5s = 200s` 不得改回小窗口：AI 启动含嵌入模型加载 + 外部探测，实测可达 100s+，短窗口会误判未就绪触发自动回滚。
8. 构建产物一律经 artifact 中转（`--build-artifacts` / `--from-artifacts` 共用 `deploy/deploy.py`），禁止在目标机 checkout 源码。
9. 并发组与「同环境串行」语义不得更改。

### C. 数据红线

10. **部署链路不执行 alembic 迁移**；库结构差异由 `backend/scripts/schema_sync.py` 兜底：默认 `prod=check（只读校验）/ test=sync（自动补列）`，仓库变量 `DEPLOY_SCHEMA_MODE` 可覆盖（off/check/sync）。
11. schema_sync 能力边界，AI 不得夸大或误用：
    - 可自动：可空或带 server_default 的新增列、缺失索引（含 UNIQUE）；
    - **阻断部署**：NOT NULL 无默认值的缺列、主键/自增列缺失（阻断发生在「上传代码之前」，线上仍跑旧版；DDL 不可回滚）；
    - 只告警绝不改：索引差异、类型/长度不一致、模型已废弃的多余列（绝不 DROP）；
    - 不处理：删列/改名/改类型/改枚举/FK/CHECK/字符集排序规则/数据回填/alembic 版本链。
12. `backend/alembic/versions/` 迁移脚本默认不入库（由远端专人执行），严禁 `git add` 提交。
13. 发版前 prod 自动全量 DB 备份（`Pre-deploy DB backup (prod)` 步骤）必须保留：脚本缺失或失败只 warning + 企微告警、不阻断部署，但备份 id 必须继续进 Summary 供回滚。
14. 所有时间字段一律 UTC（`datetime.now(UTC)`），禁裸 `datetime.now()`——本地时间落库会整体偏 8 小时。

### D. 门禁与可观测红线

15. 门禁**非阻塞** + **outcome 如实标注**：测试失败必须显示「有失败用例（非阻塞放行）」，禁止谎报「已通过」；参数校验类硬失败（非法环境、缺确认词、非法 ref）仍是硬拦。
16. AI 组件无自动门禁：必须保留 `::notice`「请确认已人工验证」提示，不得静默跳过。
17. 部署链路 API 回归只跑 `automation/ci/flow-tests.txt` 主流程用例（快照 52 条，按 node id 列出）；**用例改名或删除必须同步维护该清单**（否则 pytest 直接报错）；其余接口回归由 `test-gate.yml`（push test）与夜间任务承接，不得凭空消失。
18. 通知：`NOTIFY_WEBHOOKS` secret 按 envs 分流（开源小组群=测试环境通知、【USP】群=生产通知）；样式由主仓 Variable `NOTIFY_STYLE` 控制（当前 `image`）；通知主旨取「目标分支最近一条已合并 PR」，拿不到留空退回固定文案，不阻断部署。

### E. 流程纪律

19. **修改 `deploy-split.yml` 或任何部署脚本前，必须先 `git fetch origin test`**（有冲突合并前科），并基于最新 test 拉规范特性分支 `{模块}-{type}-{英文}` 开发，PR 合入 test 后删分支。
20. `test`/`dev` 分支保护：必须他人 approve，`--admin` 不可绕过；**禁止 merge main 进 test**。
21. 提交/PR 标题格式 `type(ORS-数字): 描述`（工单号紧跟类型前缀，scope 位不与工单号共存）；中文提交信息必须 `git commit -F <UTF-8 临时文件>`，禁 `-m` 直传。
22. PR 变更文件数量异常（经验值 >50）必须先排查是否基底错误导致的批量误删（见事故档案 #479）。
23. 验证闭环：改动后必须实测——至少完成 YAML 合法性校验 + 一次真实 run 观察（或相关 pytest / deploy/tests），**绝不谎报完成**。
24. 验证期间的仓库变量开关（`SPLIT_DEPLOY_FAST` 等）用完必须复位，不得长期残留。

## 3. 事故档案（护栏的来源；动约束前先读）

| 事故 | 经过 | 沉淀的护栏 |
|---|---|---|
| #338 误覆盖（2026-10-20） | merge main 把旧版工作流带进 test，删 73 行 CI 门禁 | #363 恢复；**禁 merge main** |
| #479 批量误删（2026-11-20） | PR 基底错误致 2138 个文件被删 | #480 当日 revert、#485 重做；**files changed 数量警惕** |
| 50a40a0 冲突（2026-10-12） | 工作流多人并行改动撞车 | **改前必 fetch 最新 test** |
| 自托管 runner 慢 | 每次重新下载 Node action（36s+78s） | 自托管 job 零 `uses:` |
| 健康检查误判回滚 | 20×3s 窗口太短，AI 慢启动被误判 | 放宽 40×5s |
| gate 被上游漂移卡死（2026-10-08） | 上游用例红稳定卡死发布 | 门禁非阻塞化 + 如实标注 |

## 4. 环境速查（无凭据）

| 项 | test | prod |
|---|---|---|
| 默认部署分支 | `test` | `dev` |
| 数据库 | `helpdesk_test` | `helpdesk_724` |
| 部署根目录 | `/data/apps/TestOpenRobotService` | `/data/apps/OpenRobotService` |
| 后端解释器 | miniconda `envs/test-backend` | `backend/.venv/bin/python` |
| 健康检查 | 9400 / 9401 / `/t/app/` | 8400 / 8401 / `/p/app/` |
| 部署脚本备份缓存 | `~/ors-ci`（同 prod 机制） | `~/ors-ci` |

> 服务器地址与全部凭据见 GitHub secrets 及内部运维文档，禁止写入任何仓库文件。

## 5. 改动前自检清单

- [ ] `git fetch origin test`，基于最新 test 拉特性分支
- [ ] 读过工作流当前真实内容（不凭记忆、不凭本文）
- [ ] 第 2 节 A~E 逐条对照
- [ ] runner 归属正确：碰生产凭据 ⇒ 自托管
- [ ] 提交/PR 格式正确（工单号紧跟类型前缀）
- [ ] 验证完成并如实汇报（YAML 校验 + 真实 run / 测试）
- [ ] 若架构事实发生变化：同一 PR 内更新本文与 README 索引
