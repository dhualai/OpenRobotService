# Test Gate 三层门禁设计（待确认）

> 状态：CI 侧（标记机制、门禁判定、工作流拆分）已实现；报告侧（首页精简、命名、分组）待 #499 合入后做。
> 关联：[#508](https://github.com/dhualai/OpenRobotService/pull/508)（部署提速，已在分支上）、[#499](https://github.com/dhualai/OpenRobotService/pull/499)（业务报告，未合入）。

## 1. 背景

`test` 上的 Test Gate 目前用四个「技术套件」判定放行：框架与基础设施、Mock API、真实后端 Smoke、OpenAPI 契约。
Mock API 是一个大桶，装了 162 条用例：既有主流程（提单→派单→处理→关闭）的步骤断言，也有后台 CRUD、分页、导出、渠道回调这类明细。

结果是两个问题：

1. **平权**：一条后台资源 CRUD 失败，会让整条流水线红，和主流程失败看起来一样严重。
2. **噪音**：报告首页和门禁摘要都按接口列，领导看不到「主流程能不能走通」。

实测数据：

| 组合 | 用例数 | 耗时 |
| --- | --- | --- |
| 全部 `-m api` | 162 | 2.4 秒 |
| 主流程子集 | 52 | 0.7 秒 |

耗时差异可以忽略，所以这次改的是**判定口径和展示口径**，不是性能。

## 2. 三层定义

| 层 | 内容 | 门禁作用 | 证据来源 |
| --- | --- | --- | --- |
| **P0 主流程** | 四条业务主线：我要摇人、知识库、学习答题、高价值问题 | 决定通过/受阻 | 业务场景用例 + UI 实跑 |
| **P1 步骤断言** | 挂在主流程步骤上的接口检查：登录、会话、提单、建单、派单/接单、状态流转、完成确认 | 失败即阻塞，首页指出卡在哪一步 | Mock 接口用例（52 条） |
| **P2 明细** | 其余接口、边界、CRUD | 只进技术明细，不阻塞 | Mock 接口用例（110 条） |

## 3. 改动清单

### 3.1 标记机制

- 新增 `automation/ci/flow-tests.txt`（本分支已加，52 条 node id）。
- 新增 `automation/conftest.py`：`pytest_collection_modifyitems` 按清单给用例打 `flow` 标记。
  - 清单按 node id 匹配；用例改名或删除时 pytest 收集不到该 id，钩子直接报错，不静默漏跑。
- `automation/pyproject.toml`：`markers` 注册 `flow`，避免 `--strict-markers` 报错。

### 3.2 门禁判定

- `automation/src/reporting/gate_verdict.py`：
  - `REQUIRED_SUITES` 由「框架、Mock API、真实 Smoke、契约」改为「框架、**主流程接口**、真实 Smoke、契约」。
  - 新增条目指向新的 JUnit/Allure 产物（`junit-quality-flow.xml`、`allure-results-quality-flow`）。
  - P2 明细不进 `REQUIRED_SUITES`，但仍随产物进报告技术明细。
  - `未验收` 规则不变：全跳过、产物缺失、产物不完整仍判未验收。

### 3.3 工作流

`.github/workflows/test-gate.yml` 的 `quality-gate` job 拆成两次 pytest：

| 步骤 | 选择器 | 产物 | 阻塞 |
| --- | --- | --- | --- |
| 主流程接口 | `-m flow` | `junit-quality-flow.xml` / `allure-results-quality-flow` | 是 |
| 其余接口 | `-m "api and not flow"` | `junit-quality-detail.xml` | 否（`continue-on-error`） |

`allure-report` job 增补 `allure-results-quality-flow` 的下载与合并；`quality-api` 相关产物保留给明细表。

### 3.4 报告

`business_report.py` / `business_scenarios.py`：

- 首页只保留「总体验收结论 + 四条关键流程的触发动作、本次验证、结果、卡点」。
- 技术明细分两段：「步骤断言」与「其他接口」。
- 模块命名按服务号功能：我要摇人 / 系统任务 / 知识库；观察项用中文名。

> **依赖**：这两份文件属于 #499，尚未合入 `test`（`business_scenarios.py` 在 `test` 上不存在）。所以报告部分必须等 #499 合入后，在新分支上做，或把 #499 合进本分支后再做。

## 4. 实施顺序

1. 标记机制 + 门禁判定 + 工作流（本分支可做，不依赖 #499）。
2. 用一次手动 Test Gate 验证：主流程 52 条全绿、门禁「通过」、明细失败不阻塞。
3. #499 合入后，做报告侧（首页精简 + 命名 + 分组）。
4. 用同一份产物做离线回归，确认首页结论与底层结果一致。

## 5. 风险

- **漏跑**：P2 不阻塞后，接口层回归只剩 push 的 Test Gate 与夜间；靠「改动路径命中必跑」补偿。
- **node id 漂移**：用例改名会让清单失配；钩子选择显式报错而不是静默跳过。
- **产物拆分**：多一份 JUnit/Allure 目录，`allure-report` 的下载矩阵要同步，漏加会导致「未验收」。

## 6. 验收标准

- [ ] `-m flow` 选中 52 条，`-m "api and not flow"` 选中 110 条，两者并集等于 162、无重复。
- [ ] 主流程用例失败时门禁判「失败」，摘要指出具体用例。
- [ ] 明细用例失败时门禁仍可「通过」，但明细表标红。
- [ ] 任一套件产物缺失或全跳过时仍判「未验收」。
- [ ] 报告首页只出现总体验收结论和四条流程。
