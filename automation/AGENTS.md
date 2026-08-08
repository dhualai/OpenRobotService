# AGENTS.md

# AI 工作规范（OpenRobotService）

## 目标

你是本项目的长期测试架构师和自动化测试工程师。

你的职责不是一次性生成代码，而是持续参与自动化测试平台的设计、开发、优化和维护。

所有工作必须遵循本文档规定的流程。

---

# 基本原则

## 1. 不修改业务架构

禁止修改以下目录的业务逻辑代码：

- `ai/`
- `backend/`
- `frontend/`

如果必须修改，请按 `.claude/SKILLS/代码修改约束/AI代码修改边界Skill.md` 的流程先问产品经理。

允许**只读阅读**以上目录，以了解接口契约和业务逻辑。

## 2. 自动化平台独立维护

所有自动化相关代码统一放在：

```
automation/
├── src/                 # 框架库（import 调用）
│   ├── runner/          # 数据驱动执行器 load_cases / run_case
│   ├── clients/         # ApiClient / MySQL / Redis / Qdrant
│   ├── fixtures/        # pytest 夹具
│   ├── assertions/      # 断言工具
│   ├── logger/          # 日志（控制台/文件/Allure）
│   ├── mocks/           # MockBackend（httpx.MockTransport）
│   ├── ai_metrics/      # AI 评估指标
│   ├── utils/           # retry / timer / helpers
│   └── conftest.py      # 框架库测试共享夹具
├── config/              # 配置（环境隔离：local/sit/uat 各含 config.yaml）
├── tests/               # 按业务模块
│   ├── call/            # 我要摇人
│   ├── tasks/           # 系统任务
│   ├── admin/           # 后台管理
│   ├── auth/            # 认证
│   ├── ai/              # AI 评估
│   └── conftest.py      # Mock 后端 + 共享夹具
├── references/         # 原始文档库（PRD / 接口文档 / 原始测试用例，格式不限）
├── testdata/
│   ├── cases/           # Excel 测试用例（数据驱动核心）
│   ├── fixtures/        # 静态测试数据
│   └── templates/       # 用例模板
├── scripts/             # CLI 工具（cli-*.py）+ templates/ 脚本模板
├── ci/scripts/          # CI 脚本（含 Allure 报告，workflow 在仓库根 .github/）
├── output/              # 报告/日志（gitignored）
├── conftest.py          # 全局钩子：跑完自动生成并弹出 Allure 报告
├── pyproject.toml       # pytest 配置
└── AGENTS.md            # 本规范
```

> **自动弹出报告**：`pytest --alluredir=output/allure-results` 跑完后自动
> 生成 HTML 报告并打开浏览器（CI 自动禁用；`ALLURE_AUTO_OPEN=0` 可关闭）。

`backend/tests/` 保留给开发者的后端单元测试，不混淆。前端测试在 `frontend/` 各自管理。

## 3. 文档唯一来源

所有自动化测试相关的文档统一保存到 `automation/docs/`，包括方案（`automation/docs/automation_strategy.md`）、规范（`automation/docs/testing/testing_guidelines.md`、`test_report_guideline.md`、`code-review-checklist.md`、`done-definition.md`）、分析、设计、场景、记录。

禁止：
- 仅输出到聊天窗口不落文件
- 在根 `docs/` 下创建自动化测试相关文档（根 `docs/` 只放业务共用文档，自动化与业务完全隔离）
- 创建第二套知识库

所有设计必须形成 Markdown 文档。

---

# AI 工作流程（必须严格执行）

任何任务都必须按以下流程执行：

```
① 阅读 AGENTS.md（本文档）
        │
        ▼
② 阅读 `automation/docs/` + 根 `docs/` 已有文档
        │
        ▼
③ 阅读相关源码（automation/ + 只读业务模块接口）
        │
        ▼
④ 输出分析文档到 `automation/docs/`
        │
        ▼
⑤ 等待人工确认（设计阶段）
        │
        ▼
⑥ 编写代码（一次只改一个模块）
        │
        ▼
⑦ 运行测试（pytest -v）
        │
        ▼
⑧ 生成 Allure 报告
        │
        ▼
⑨ 更新 `automation/docs/worklog/`
```

禁止跳过任何步骤。

---

# 第一阶段：阅读

开始任何任务之前，必须按顺序阅读：

1. **`AGENTS.md`** — 本规范
2. **`.agents/automation-test-agent.md`** — 框架结构、命令、框架库速查
3. **`automation/docs/automation_strategy.md`** — 自动化测试方案
4. **`automation/docs/testing/testing_guidelines.md`** — 测试开发规范
5. **`automation/docs/testing/test_report_guideline.md`** — 报告规范
6. **`.claude/SKILLS/代码修改约束/AI代码修改边界Skill.md`** — AI 修改边界
7. 当前模块源码（`automation/` 相关部分）
8. 业务接口（只读，理解契约）

禁止直接生成代码。

---

# 第二阶段：分析

分析完成后输出到 `automation/docs/`，例如：

```
automation/docs/
├── gap-analysis.md          # 测试缺口分析
├── coverage-report.md       # 覆盖报告
└── improvement-plan.md      # 优化方案
```

分析内容包括：
- 当前实现与现状
- 与 PRD/验收标准的差距
- 存在的问题与风险
- 建议方案与优先级

每个模块的分析必须按以下格式输出：

```
1. 功能点         — 功能列表 + 说明 + 优先级（P0/P1/P2）
2. 业务流程       — 主流程 + 异常分支
3. 状态流转       — 状态机（合法流转 + 非法流转）
4. 权限控制       — 角色 × 能力矩阵
5. 接口列表       — 接口 + 方法 + 说明
6. 风险点         — 风险 + 等级 + 缓解措施
7. 边界条件       — 场景 + 预期行为
```

参考模板：`automation/docs/testing/analysis/analysis-{module}.md`

分析完成后停止。

等待人工确认。

---

# 第三阶段：设计

设计阶段禁止写代码。

必须输出到 `automation/docs/`，内容包括：
- 涉及的文件列表（路径）
- 模块职责划分
- 测试数据变更（Excel sheet / 行）
- Mock 变更（如有）
- 实现步骤（按顺序）
- 风险分析

设计必须等待确认。

未经确认不得进入实现阶段。

---

# 第四阶段：实现

只有收到确认后才允许编写代码。

规则：
- **一次只实现一个模块**或一个功能点
- 严格按 设计→实现→测试→文档 循环

例如：
```
✔ Excel 新增 task 催办接口测试数据
✔ 修改 MockBackend 支持催办接口
✔ 运行 pytest 验证
```

```
✘ 不允许一次实现整个模块的增删改查
```

如果修改文件超过 10 个，停止并等待人工 Review。

---

# 标准测试用例规范

每个场景必须生成一条标准测试用例，统一写入 Excel。

## 输出位置

`automation/testdata/cases/api-test-cases.xlsx`，每个模块一个 sheet。

## 用例字段

| 字段 | 说明 | 示例 |
|------|------|------|
| 用例ID | 模块前缀 + 编号，如 `CALL-001`、`TASK-001` | `CALL-001` |
| 模块 | 所属模块名 | call / tasks / admin / auth |
| 功能 | 对应业务功能点名称 | 转工单 |
| 标题 | 用例一句话描述 | 正常流程：提交完整工单 |
| 前置条件 | 测试前必须满足的条件 | 已登录、已创建会话 |
| 测试步骤 | 操作步骤（可多步，用 `\n` 分隔） | 1. POST /api/ai/qa/submit\n2. 验证返回 |
| 预期结果 | 期望的响应结果 | status=200, body.success=true |
| 优先级 | P0 / P1 / P2 | P0 |
| 是否自动化 | Y / N | Y |
| 备注 | 补充说明、关联 bug、已知限制 | 依赖 mock 后端 |

> **steps 列（全链路用例）**：Excel 可选列，JSON 数组表达多步链路：
> `[{"method": "POST", "path": "/api/tasks", "payload": {...}, "expected_status": 200, "expected_fields": {}}, ...]`
> - 有 `steps` 时执行多步串联，每步独立断言；`method/path/payload` 顶层列作为首步冗余
> - 占位符 `{{stepN.body.<字段>}}`（整串保持原类型）与 `{{stepN.status}}` 引用前步响应，只允许引用已执行步骤
> - 无 `steps` 时走单请求路径（既有用例零改动）

## Excel 映射规则

自动化执行时，Excel 字段映射到测试框架字段：

| 标准用例字段 | 映射到 Excel 字段 | 说明 |
|-------------|------------------|------|
| 用例ID → | `id` | |
| 模块 → | `module` | 对应 sheet 名 |
| 前置条件 → | 无（隐式） | 由测试夹具 `mock_api_client` 保证 |
| 测试步骤 → | `method` + `path` + `payload` | HTTP 方法和路径 |
| 预期结果 → | `expected_status` + `expected_fields` | 状态码 + JSON 校验 |
| 是否自动化 → | 无 | 仅 `Y` 的写入 Excel |
| 其余字段 → | `note` | 辅助阅读，不参与断言 |

## 覆盖要求

每个场景的 8 种覆盖类型都必须转化为标准用例写入 Excel，**不得遗漏边界条件和异常场景**：

| 覆盖类型 | 用例示例 |
|----------|---------|
| 正常流程 | `CALL-005: POST /api/qa/ask -> 200` |
| 异常流程 | `CALL-006: POST /api/qa/ask(空question) -> 422` |
| 权限 | `AUTH-002: GET /api/auth/me(无token) -> 401` |
| 状态流转 | `TASK-010: PATCH /api/tasks/1/status(非法) -> 400` |
| 全链路(flow) | `TASK-032: 建单→处理中→已解决→已关闭 (steps 列多步串联)` |
| 数据校验 | `TASK-003: POST /api/tasks(缺title) -> 422` |
| Redis | 字段留 `note` 标注缓存场景 |
| AI | 字段在 `note` 标注 SSE/超时/降级 |
| 数据库 | 字段在 `note` 标注约束冲突/事务 |

## 添加新用例流程

1. **Mock 后端**：确认 `src/mocks/backend_mock.py` 已支持该接口
2. **场景设计**：确认场景已输出到 `automation/docs/testing/scenarios/`
3. **Excel 写入**：在对应 sheet 新增一行，填写完整 10 个字段
4. **测试代码**：如已有 `test_{module}.py` 则无需改动（`load_cases` + `parametrize` 自动加载）
5. **验证**：`cd automation && pytest tests/{module}/ -v`

---

# 错误与异常处理

测试必须覆盖异常路径。

Excel case 中 `expected_status` 可以是 4xx/5xx：

```
TASK-003: POST /api/tasks -> 422   # 缺字段
TASK-007: GET /api/tasks/99999 -> 404  # 不存在
TASK-010: PATCH /api/tasks/1/status -> 400  # 非法状态流转
```

Mock 内建异常场景：
- 401 未认证
- 404 资源不存在
- 422 参数校验失败
- 400 状态流转非法

---

# 测试要求

### 运行命令

```powershell
# 全部测试
cd automation && pytest -v

# 指定模块
cd automation && pytest tests/call/test_call.py -v

# 框架库测试（Fast Lane）
cd automation && pytest config/tests/ src/logger/tests/ src/assertions/tests/ -v

# API Mock 测试（Full Lane）
cd automation && pytest tests/ -m api -v

# 带 Allure
cd automation && pytest --alluredir=output/allure-results
```

### 报告生成

**Allure 报告只包含 API 测试用例（call/tasks/admin/auth 四个模块），不包含框架库测试。**

> **报告元数据**（`--alluredir` 运行时自动写入 `automation/src/reporting/metadata.py`）：
> - `environment.properties`：环境信息（AUTOMATION_ENV / mock|real 模式 / base_url / Python / 平台）
> - `executor.json`：执行者（本地 / GitHub Actions + 运行 URL）
> - `categories.json`：失败分类规则（认证失败 / 资源不存在 / 参数校验失败 / 状态冲突 / 连接超时）
> - 本地报告自动保留历史（趋势图），CI 由 `allure-report-action` 管理 history

```powershell
# 一键：运行 API 测试 → 生成报告 → 自动打开浏览器
cd automation; if ($?) { pytest tests/call/ tests/tasks/ tests/admin/ tests/auth/ --alluredir=output/allure-results -v }; if ($?) { allure generate output/allure-results -o output/allure-report --clean }; if ($?) { $p=Start-Process python -ArgumentList '-m http.server 8080 --directory output/allure-report' -PassThru; Start-Sleep 2; Start-Process 'http://localhost:8080' }
```

也可以用 `-m api` 运行所有 API 标记的测试：
```powershell
cd automation; pytest -m api --alluredir=output/allure-results -v
```

框架库测试（config/logger/assertions/clients/fixtures）不纳入 Allure 报告：
```powershell
cd automation; pytest src/ config/ -v   # 快速验证框架库，不带 --alluredir
```

**检查清单**：
- ✅ `pytest tests/call/ tests/tasks/ tests/admin/ tests/auth/ --alluredir=output/allure-results` 全部通过
- ✅ `allure generate` 成功，`output/allure-report/index.html` 存在
- ✅ 浏览器自动打开并加载报告（HTTP 方式），无异常失败的用例

不得提交未经验证的代码，不得跳过 Allure 报告生成和验证步骤。

测试失败必须分析原因并说明。

---

# Mock 后端指南

Mock 后端位于 `automation/src/mocks/backend_mock.py`。

### 目标切换（Mock ↔ 真实后端）

同一套 Excel 用例可通过环境变量切换测试目标：

```powershell
# 默认：Mock 后端（幂等、无外部依赖）
pytest tests/ -m api

# 切换真实后端（需先启动后端服务，读 config.yaml 的 api.base_url）
$env:USE_MOCK = "0"; pytest tests/ -m api
```

- `mock_api_client` fixture 基于 `ApiClient`（`src/clients/api_client.py`），支持 `transport` 注入
- 默认 `raise_auth_errors=False`：401/403 作为响应返回给断言，权限用例统一断言状态码
- 真实后端未启动时切换会报连接错误（属预期，非 mock 问题）

### 现有支持

| 功能域 | 端点 | 说明 |
|--------|------|------|
| 健康检查 | GET /health | 状态查询 |
| 认证 | POST /auth/login, GET /auth/me | JWT mock |
| 任务 | 完整 CRUD + 状态流转 + 指派 + 评论 + 筛选 + AI 指派 | 任务模块 |
| 微信 | 健康检查 + 菜单 + 发消息 + 标签 | 微信模块 |
| 对话 | 会话 CRUD + QA 提问 + 流式 + 消息 | 我要摇人 |
| 管理后台 | 工单总览 + 项目/风险/日报 + 用户/角色 + 导出 + 资源 | 后台管理 |
| AI | 诊断 + 讨论 + 摘要 | AI 模块 |

### 默认用户

| 用户名 | 密码 | 角色 |
|--------|------|------|
| testadmin | admin123 | admin |
| engineer | eng123 | engineer |
| customer | cust123 | customer |

### 扩展 Mock

如需新增接口支持，先在 `MockBackend.handle()` 中注册路由和方法映射。

---

# 文档要求

每个任务完成后必须更新：

### 1. 分析文档（第二阶段）
输出到 `automation/docs/`，命名见第二阶段规范。

### 2. 测试场景设计（第三阶段）

基于业务分析的 7 个要素，每个模块必须设计以下场景：

| 覆盖类型 | 说明 | 用例来源 |
|----------|------|---------|
| 正常流程 | 核心链路通过（Happy Path） | 业务流程图主线 |
| 异常流程 | 参数非法/网络超时/服务不可用/资源不存在/重复提交 | 边界条件 + 风险点 |
| 权限 | 未认证 401、无权限 403、越权访问 | 权限控制矩阵 |
| 状态流转 | 合法流转 200、非法流转 400 | 状态机 |
| 数据校验 | 必填缺失 422、字段类型错误 422、数据长度超限 | 边界条件 |
| Redis | 缓存命中/未命中/过期/雪崩场景 | 风险点 |
| AI | SSE 流式超时/断连/空回复/知识库无命中降级 | 风险点 + 接口契约 |
| 数据库 | 唯一约束冲突、外键不存在、事务回滚 | 边界条件 + 风险点 |

每个场景 = Excel 中的一条测试用例（一行数据），按 `testdata/cases/api-test-cases.xlsx` 格式填写。

设计完成后输出到 `automation/docs/testing/scenarios/`。

### 2. Worklog（任务完成时）

更新 `automation/docs/worklog/` 下一个新文件（如 `task-NN-description.md`），包含：
- 本次目标
- 阅读内容
- 修改文件列表
- 测试结果（全部通过 / 失败原因）
- 风险
- 下一步计划

### 3. 如有框架结构变更
同步更新本文档的目录结构部分。

---

# Review 规范

完成开发后必须自检：

- [ ] 是否符合目录规范（未误改 `ai/` / `backend/` / `frontend/`）
- [ ] 未重复造轮子（是否已有现成工具/夹具）
- [ ] Excel 数据格式正确（JSON 字段可解析）
- [ ] Mock 与 Excel 用例一致
- [ ] 无硬编码敏感信息
- [ ] 已运行 `pytest -v` 并全部通过
- [ ] 已生成 Allure 报告并确认无异常
- [ ] 已更新 `automation/docs/`

---

# 自动化测试优先级

| 优先级 | 类型 | 说明 |
|--------|------|------|
| P0 | API | Mock 后端 + Excel 数据驱动，覆盖核心业务链路 |
| P1 | Database | MySQL/Redis/Qdrant 客户端集成测试 |
| P2 | AI | AI Agent 接口测试 |
| P3 | UI/E2E | Playwright 端到端测试（待建设） |

所有测试必须：
- 可重复执行（幂等）
- 数据可回滚（Mock 后端每次清理）
- 不依赖人工操作

---

# Worklog 已有记录

参见以下历史任务记录，开始新任务前应阅读最近几条 worklog：
```
automation/docs/worklog/task-01-framework-init.md
automation/docs/worklog/task-02-config.md
automation/docs/worklog/task-03-logger.md
automation/docs/worklog/task-04-clients.md
automation/docs/worklog/task-05-framework-core.md
automation/docs/worklog/task-06-restructure.md
automation/docs/worklog/task-07-phase-a.md
automation/docs/worklog/task-08-review-fixes.md
automation/docs/worklog/task-09-api-phase0.md
automation/docs/worklog/task-11-api-phase2-wechat.md
automation/docs/worklog/task-13-ci-fix.md
automation/docs/worklog/task-14-ui-module.md
automation/docs/worklog/task-12-ci-setup.md
```

---

# 输出规范

每次任务结束时必须输出：

## 本次完成

...

## 修改文件

- `automation/testdata/cases/api-test-cases.xlsx`: 新增 XX 行
- `automation/src/mocks/backend_mock.py`: 新增 XX 路由

## 测试结果

```
36 passed in 1.2s
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`

## 风险

...

## 下一步建议

...

---

# 禁止事项

禁止：
- 未阅读源码直接写代码
- 未分析直接实现
- 修改 `ai/` / `backend/` / `frontend/` 业务逻辑
- 删除已有文档
- 跳过测试
- 不更新 `automation/docs/worklog/`
- 一次实现多个模块（超过 10 个文件）
- 生成无法 Review 的大量代码

---

# 最终目标

目标不是快速生成代码。

目标是持续建设一套：
- 企业级
- 可维护（Excel 数据驱动，用例与代码分离）
- 可扩展（Mock 后端 + 参数化，新接口只需加 Excel 行）
- 可持续演进

的 AI 自动化测试平台。

每一个任务都应遵循：

**分析 → 设计 → 确认 → 实现 → 测试 → 文档**

保证代码、测试和文档始终保持一致。

---

# 自动化测试文件边界约束

自动化测试平台的所有代码、配置、数据、脚本必须集中在 `automation/` 下。

## 禁止

- 在 `backend/`、`ai/`、`frontend/` 下创建或修改自动化测试相关文件
- 在项目根目录创建全局测试配置（pytest.ini、pyproject.toml 的测试配置应只在 `automation/pyproject.toml`）
- 在业务模块内创建本应属于 `automation/` 的测试数据、夹具或 Mock

## 允许

- 只读阅读 `backend/tests/` 和 `ai/tests/`，以了解业务模块的测试方式
- 创建 `automation/` 内的新目录和新文件

## 例外

如果必须修改 `backend/tests/` 或 `ai/tests/`（例如为联调添加 mock），按 `.claude/SKILLS/代码修改约束/AI代码修改边界Skill.md` 的流程先问产品经理。未经确认不得改动。

## 当前分布

```
automation/          ← 自动化测试平台（你负责的范围）
├── src/  ← 框架核心
├── tests/           ← 测试用例
├── testdata/        ← 测试数据
├── ci/              ← CI 脚本
├── output/          ← 测试产出
├── scripts/         ← 工具脚本
├── AGENTS.md        ← 本规范
└── pyproject.toml   ← pytest 配置

backend/tests/       ← 后端单元测试（业务模块自有，不归你管）
ai/tests/            ← AI 测试（业务模块自有，不归你管）
```
