# AI Agent 工作流程

> 本文件定义 AI Agent 在 OpenRobotService 项目中执行任务时的行为规范。
> **职责分工**：本文件只描述工作流程，不包含业务知识。
> 自动化测试框架见 `automation/AGENTS.md`，测试规范见 `automation/docs/testing/testing_guidelines.md`，业务规则见 `docs/business_rules.md`。

---

## 一、工作前必读

在编辑任何代码前，按以下顺序加载上下文：

1. **`docs/project_architecture.md`** — 项目架构、模块划分、技术栈
2. **`automation/AGENTS.md`** — 自动化测试框架 Agent（框架结构、命令、报告）
3. **`automation/docs/automation_strategy.md`** — 自动化测试方案总览
4. **`automation/docs/testing/testing_guidelines.md`** — 测试开发规范（命名、断言、夹具等）
5. **`docs/business_rules.md`** — 业务约束、状态机、权限规则
6. **`docs/PRD.md`** — 产品需求文档（功能点规格、验收标准）

---

## 二、修改代码流程

### 2.1 分析阶段
- 搜索代码库确认影响范围
- 确认是否符合 `docs/business_rules.md` 定义的业务规则

### 2.2 实现阶段
- 遵循现有代码风格（类型注解、导入顺序、命名约定）
- 新增逻辑必须附带对应的单元测试或集成测试
- 测试框架结构与命令参考 `automation/AGENTS.md`（工作流入口见 `.agents/skills/automation-testing/SKILL.md`）
- 测试编写规范参考 `automation/docs/testing/testing_guidelines.md`
- Mock 和 Fixture 参考 `automation/src/mocks/backend_mock.py` 的现有模式
- 测试数据在 `automation/testdata/cases/api-test-cases.xlsx` 中管理

### 2.3 验证阶段
```powershell
# 自动化框架测试
cd automation && pytest -v

# 后端测试（如涉及 backend/ 改动）
cd backend && pytest --ignore=tests/tasks

# 前端测试（如涉及前端改动）
cd frontend && npm run test
```

### 2.4 提交阶段
- 遵循 Conventional Commits：`feat:` / `fix:` / `test:` / `refactor:` / `docs:` / `chore:`
- 提交前逐项检查 `automation/docs/testing/code-review-checklist.md`
- 如果更新了测试用例 Excel，同步更新文档；运行 `python automation/scripts/cli-generate-report.py` 生成报告

---

## 三、测试操作速查

| 操作 | 命令 | 参考 |
|------|------|-------|
| 运行自动化框架全部 | `cd automation && pytest` | `automation/AGENTS.md` |
| 运行框架库测试 | `cd automation && pytest src/ config/` | `automation/AGENTS.md` |
| 运行 API Mock 测试 | `cd automation && pytest tests/ -m api` | `automation/AGENTS.md` |
| 带 Allure 运行 | `cd automation && pytest --alluredir=output/allure-results` | `automation/AGENTS.md` |
| 生成 Allure 报告 | `cd automation && allure generate output/allure-results -o output/allure-report --clean` | `automation/AGENTS.md` |
| 打开 Allure 报告 | `cd automation && python -m http.server 8080 -d output/allure-report` | `automation/AGENTS.md` |
| 一键测试+报告 | `automation/ci/scripts/run-full-lane.bat` | `automation/AGENTS.md` |
| 运行后端后端 | `cd backend && pytest --ignore=tests/tasks` | 后端 README |
| 运行前端 | `cd frontend && npm run test` | 前端 README |
| 前端覆盖率 | `cd frontend && npm run test:coverage` | 前端 README |
| 生成 Excel 测试报告 | `python automation/scripts/cli-generate-report.py` | `scripts/` |

---

## 四、文档维护规则

| 变更场景 | 需更新的文档 |
|----------|-------------|
| 新增测试模式或工具 | `automation/AGENTS.md` |
| 调整测试框架结构 | `automation/AGENTS.md` |
| 调整命名/断言/夹具规范 | `automation/docs/testing/testing_guidelines.md` |
| 调整测试策略 | `automation/docs/automation_strategy.md` |
| 新增故障排查经验 | `docs/troubleshooting.md` |
| 新增业务规则 | `docs/business_rules.md` |
| 新增测试命令 | `automation/AGENTS.md` |
| 更新 Excel 用例清单 | 运行 python automation/scripts/cli-generate-report.py 刷新 |
| 本文流程变更 | `AGENT.md` |

---

## 五、参考文档索引

| 文档 | 用途 |
|------|------|
| **测试相关** | |
| `automation/AGENTS.md` | 自动化测试框架 Agent（命令/结构/报告） |
| `automation/docs/automation_strategy.md` | 自动化测试方案（分层/策略/CI） |
| `automation/docs/testing/testing_guidelines.md` | 测试开发规范（命名/断言/夹具） |
| `automation/docs/testing/test_report_guideline.md` | 报告规范（Allure/pytest-html） |
| `automation/docs/testing/code-review-checklist.md` | 代码 Review 清单 |
| **业务文档** | |
| `docs/project_architecture.md` | 项目架构说明 |
| `docs/business_rules.md` | 业务规则 |
| `docs/troubleshooting.md` | 常见问题排查 |
| `docs/PRD.md` | 产品需求文档 |
| `backend/CODEBASE_OVERVIEW.md` | 后端代码结构总览 |
| `backend/INTEGRATION_DESIGN.md` | 外部任务源集成设计 |
| `CONTRIBUTING.md` | 贡献指南 |
