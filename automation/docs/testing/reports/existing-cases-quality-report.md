# 存量自动化测试质量报告

- **报告日期**：2026-11-16
- **分析对象**：`automation/tests/` 全部存量用例 + 9 条 CI 门禁工作流（远端实测）
- **代码基线**：新仓库 `D:\WorkCode\OpenRobotService-new`，test 分支 @ `0547c497`（PR #458 合并提交）
- **范围声明**：本报告只评估与改善**现有用例**，不新增用例。事实基于静态代码盘点 + GitHub Actions API 实测数据。
- **数据时效说明**：CI 数据于 2026-11-16 通过 GitHub Actions API 重新实拉，覆盖远端全部 9 条工作流。原稿"8 条工作流"基于本地文件清单，与远端不一致，已更正：远端实际为 9 条（`ai-test` / `ci-guard` / `db-tests` / `deploy-split` / `real-access-check` / `test` / `ticket-gate` / `ui-regression` / `ui-smoke`），本地的 `ai-eval.yml` / `deploy.yml` / `rollback.yml` 在远端已不存在。

---

## 1. 执行摘要

存量用例共 **19 个测试文件 / 173 个测试函数**，另有 AI golden case 数据驱动 28 条、UI 纯前端用例 10 条。整体分层覆盖完整（API / DB / 契约 / 场景 / UI / AI），CI 门禁 8 条工作流。

**健康度结论：结构可用、断言偏薄、数据驱动不足、部分用例长期 skip。**

| 维度 | 评级 | 一句话 |
|---|---|---|
| 分层与标记 | 良好 | 173/173 用例均有标记（逐函数或模块级 `pytestmark`），车道划分清晰 |
| 断言强度 | 待改善 | 至少 31 处仅断言 `status=200`、无响应字段校验，属"通了就算过" |
| 数据驱动 | 偏弱 | 130 个 mock API 用例全部硬编码，仅 AI 用例用了 `parametrize` |
| 环境解耦 | 部分问题 | 凭据散落在用例文件内，多组开关型 skip 缺少统一配置入口 |
| 业务场景价值 | 良好 | 6 条真实后端场景用例已 CI 全绿，链路闭环 |
| 报告可读性 | 良好 | 全量 allure 装饰器 + docstring 首行覆盖类型，符合团队约定 |

**优先改善三件事**（详见第 4 节）：① 补齐弱断言；② 收敛硬编码凭据；③ 刷新过时文案与 skip 策略。

---

## 2. 用例资产盘点

### 2.1 分层分布

| 层级 | 文件数 | 函数数 | 说明 |
|---|---|---|---|
| API 代码驱动（mock 快车道） | 4 | 130 | call 41 / tasks 32 / admin 45 / auth 12 |
| DB 集成（真实 MySQL） | 2 | 17 | knowledge_db 12 / infrastructure 5 |
| AI 评估（数据驱动） | 4 | 4 函数 / 28 golden case | assigner 6 / data_analysis 4 / diagnosis 10 / rag_retrieval 8 |
| 业务链路（mock E2E） | 1 | 1 | 276 行单用例，U1→U2 全链路 |
| 真实后端（real） | 3 | 10 | smoke 3 / lifecycle 1 / scenarios 6 |
| UI 自动化 | 4 | 10 | login 2 / call smoke 1 / real safe api 6 / 回归 1 |
| 契约（Schemathesis） | 1 | 1 | 需 `CONTRACT_TEST_ENABLED=1` |
| **合计** | **19** | **173 函数** | — |

### 2.2 标记与车道映射

- `api` `mock`：`call/tasks/admin/auth/test_*_code.py`、business_chain、real 系列（real 系列不带 mock）
- `db`：`admin/test_knowledge_db.py`、`infrastructure/test_infrastructure_contracts.py`（db-tests 工作流 `-m db` 采集）
- `smoke`：`real/test_real_backend_smoke.py`、`ui/test_login_smoke.py`、`ui/test_real_safe_api_smoke.py`
- `e2e`：`real/test_ticket_scenarios.py`、`real/test_ticket_lifecycle_real.py`、business_chain、UI 回归
- `ai` `judge`：4 个 AI 评估文件
- `contract`：OpenAPI 契约
- 标记写法两代并存：代码驱动文件逐函数 `@pytest.mark.api`；其余用模块级 `pytestmark = [...]`。功能等价，但风格不统一（改善项 F6）。

### 2.3 条件跳过（skip）盘点

| 触发条件 | 影响用例 | 性质 |
|---|---|---|
| `USE_MOCK=0` 未满足 | real smoke 3 / lifecycle 1 / scenarios 6 | 设计如此（真实后端专用），非缺陷 |
| MySQL 不可用 / 缺表 / 非 STRICT | knowledge_db、infrastructure | 环境守卫，合理 |
| AI runtime 依赖或 judge 不可用 | AI 评估 4 个文件 | 服务型 skip，需关注"假绿" |
| `CONTRACT_TEST_ENABLED=1` 未设 | 契约 1 | 默认跳过，建议纳入常规车道 |
| `PLAYWRIGHT_E2E_ENABLED` / 账号环境变量缺失 | call UI smoke 1 | 需补配置文档 |
| `REAL_U1/U2_*` 账号缺失 | lifecycle 1 | 真实账号守卫 |

---

## 3. CI 门禁现状

远端实际 9 条工作流（2026-11-16 GitHub Actions API 实测）：

| 工作流 | 最近一次 | 近 6 次 | 说明 |
|---|---|---|---|
| `ci-guard.yml` | 通过 #23 | 全通过 | 结构级校验 |
| `ticket-gate.yml` | 通过 #303 | 全通过 | test 分支门禁 |
| `deploy-split.yml` | 通过 #144 | ✓✓×××× | 曾连败 6 次，#143 起转绿；scenarios **6 passed** |
| `db-tests.yml` | 通过 #8 | ✓✓---✓ | 12 passed / 5 skipped |
| `ui-smoke.yml` | 通过 #206 | ✓✓✓✓×× | 曾连败 4 次后转绿 |
| `real-access-check.yml` | 通过 #1 | 仅 1 次 | 真实环境连通检查 |
| `ai-test.yml` | **失败 #153** | 全失败 | develop 分支，AI 生成并执行测试失败 |
| `test.yml` | **失败 #1211** | 全失败 | 旧主测试：AI 单测非阻塞 / 真实后端 smoke / Allure 报告 |
| `ui-regression.yml` | **失败 #49** | 全失败 | 停更于 09-24，UI 链路与真实环境冒烟失败 |

**已知遗留**：
- deploy-split 的 AI unit tests (non-blocking) 仍 5 failed，属 `ai/` 目录业务代码，超出 automation 修改边界。根因已出专项报告 `automation/docs/testing/analysis/analysis-ai-unit-tests.md`，不拦截部署。
- `ui-regression` / `test` / `ai-test` 三条持续失败工作流的处置建议（修复 / 下线 / 排期）见领导版摘要 `reports/leadership-report.html`。

---

## 4. 存量用例改善清单（不新增用例）

按"投入产出比"排序，P0 优先。所有改动只触及 `automation/`。

### P0-1 补齐弱断言（✅ 第一批已完成，2026-11-16）

- **问题**：`expected_status=200)` 且无 `expected_fields` 的调用——报告估算 31 处，精扫确认 **29 处**（call 9 / tasks 7 / admin 11 / auth 2）。
- **风险**：接口返回 200 但数据错误（如删除未生效、状态没变）时用例仍通过。
- **实际改法**（较原方案调整）：逐条核对 mock 返回结构后分层处理——
  - 返回为固定 dict / 稳定字段的 **14 处补真断言**（如 `ai-assign` 的 `method/assigned_to`、resources 的 `total=0`、wechat 的 `tags`）；
  - 顶层 list / 动态计数 / SSE 流式的 **17 处加注释说明**（`expected_fields` 仅支持 dict，动态字段强行断言会让用例变脆），避免下轮被当成遗漏。
- **验收**：✅ 弱断言处或补断言、或注明理由，无"裸 200"。详见 `task-55-existing-cases-improvement-batch1.md`。

### P0-2 收敛硬编码凭据（✅ 第一批已完成，2026-11-16）

- **问题**：call/tasks/admin 三文件顶部 `_ROLE_CREDENTIALS` 明文；auth 文件内联正确凭据；business_chain 的 `U1/U2` 明文账号密码。
- **实际改法**：新建 `automation/src/config/credentials.py`，提供 `role_credentials(role)`（环境变量优先，默认值严格对齐 Mock 内置用户）；5 个测试文件改调此入口。故意用错密码/空用户名的异常用例保留原字面值（属测试语义，不收敛）。
- **验收**：✅ `grep -rn "cust123\|eng123\|123456\|_ROLE_CREDENTIALS" tests/{call,tasks,admin,auth,business_chain}/` 无残留（已实测）。不设环境变量时行为与改造前零差异。

### P1-3 巨型单用例结构化（business_chain）

- **问题**：`test_call_to_ticket_close.py` 单文件 276 行、单用例串起登录→提单→派单→处理→关闭，任一步失败整条红，定位成本高。
- **改法**：不拆成多用例（不增数量），用 `allure.step` 把每个阶段包成具名步骤 + 失败时 attach 中间响应，让 Allure 报告能定位到具体阶段。
- **验收**：Allure 报告该用例展示 5+ 具名步骤，失败步骤可直接看到请求/响应。

### P1-4 AI 评估 skip 防"假绿"

- **问题**：4 个 AI 文件在 runtime 依赖或 judge 不可用时 `pytest.skip`，全 skip 时 ai-eval 仍可能 success。
- **改法**：在 ai-eval 工作流加 skip 率阈值检查（skip/total > 80% 则标 warning 或 fail）；用例侧保留现有清楚 skip 理由。
- **验收**：CI 能区分"真实评估通过"与"全部跳过"。

### P1-5 契约测试纳入常规车道

- **问题**：`test_openapi_contract.py` 默认 `CONTRACT_TEST_ENABLED=1` 才跑，长期 skip。
- **改法**：在 ci-guard 或 db-tests 加一个轻量 job 设 `CONTRACT_TEST_ENABLED=1` + `CONTRACT_PHASES=examples` + `CONTRACT_MAX_EXAMPLES=3`，只验安全路径（当前默认 `^/api/(health|auth/login)$`）。
- **验收**：每次 push 有契约结果产出，失败可见。

### P2-6 标记风格与文案刷新

- **问题 1**：逐函数 `@pytest.mark.api`（130 处）与模块级 `pytestmark` 并存。
- **改法**：代码驱动 4 个文件顶部加 `pytestmark = [pytest.mark.api]`，删逐函数重复标记（保留 `@allure.*`）。
- **问题 2**：`tests/conftest.py` skip 提示仍写"请先执行 alembic upgrade head"，项目已废弃 alembic。
- **改法**：改为"请先执行 `Base.metadata.create_all`（CI 建表方式）或运行 db-tests 工作流 seeding"。
- **验收**：`grep -rn "alembic" tests/` 无过时引导。

---

## 5. 改善路线图（建议批次）

| 批次 | 内容 | 触及文件 | 验证方式 |
|---|---|---|---|
| 第一批 | P0-1 弱断言 + P0-2 凭据收敛 | call/tasks/admin/auth 4 文件 + config | 本地 mock 车道全量 + workflow_dispatch 预验证 |
| 第二批 | P1-3 场景结构化 + P1-4 skip 防假绿 | business_chain + ai-eval.yml | deploy-split 场景 6 passed + ai-eval skip 率可见 |
| 第三批 | P1-5 契约车道 + P2-6 标记/文案 | contract + ci-guard + 4 文件 + conftest | 契约有结果、标记统一、无过时文案 |

每批走团队流程：分析 → 设计 → 确认 → 实现 → 测试 → 文档；每批更新 worklog 与本报告状态列。

---

## 6. 风险与约束

- **修改边界**：仅 `automation/`，不碰 `ai/`、`backend/`、`frontend/` 业务代码。AI unit tests 5 failed 由 AI/后端团队跟进。
- **环境**：本机无 MySQL/Docker，DB 用例真实验证只能靠 CI db-tests；本地仅做收集级或 mock 车道验证。
- **CI 触发**：push 触发历史不可靠，验证统一走 workflow_dispatch 任意分支预验证（已实证）。
- **仓库状态**：新仓库 `D:\WorkCode\OpenRobotService-new` 干净可用；旧仓库 `D:\WorkCode\OpenRobotService` 对象库损坏已弃用（仅保留其 `.workbuddy/memory` 作为工作日志目录）。

---

## 附录：统计口径

- 函数数按 `grep -c "def test"` 逐文件统计，AI 的 golden case 按 `testdata/fixtures/ai/*.json` 的 `cases[].id` 计数。
- 弱断言按 `expected_status=200)` 且无 `expected_fields` 的文本模式统计，为下限估计（不排除个别合理场景）。
- "最近已验证结果"指本会话中通过 GitHub API + job 日志实际确认的运行，非推测。
