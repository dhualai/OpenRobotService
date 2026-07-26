# AI Agent 工作流程

> 本文件定义 AI Agent 在 OpenRobotService 项目中执行任务时的行为规范。
> **职责分工**：本文件只描述工作流程，不包含业务知识。
> 详细测试规范见 `docs/testing/` 合集，业务规则见 `docs/business_rules.md`。

---

## 一、工作前必读

在编辑任何代码前，按以下顺序加载上下文：

1. **`docs/project_architecture.md`** — 项目架构、模块划分、技术栈
2. **`docs/testing/index.md`** — 自动化测试规范总览（入口）
3. **`docs/business_rules.md`** — 业务约束、状态机、权限规则
4. **如果涉及新增测试** → `docs/testing/development-workflow.md`

---

## 二、修改代码流程

### 2.1 分析阶段
- 搜索代码库确认影响范围
- 确认是否符合 `docs/business_rules.md` 定义的业务规则

### 2.2 实现阶段
- 遵循现有代码风格（类型注解、导入顺序、命名约定）
- 新增逻辑必须附带对应的单元测试或集成测试
- 测试文件位置按 `docs/testing/directory-structure.md` 放置
- 命名规范按 `docs/testing/naming-conventions.md` 执行
- Mock 和 Fixture 按 `docs/testing/fixture-and-mock.md` 执行
- 测试数据按 `docs/testing/test-data.md` 管理

### 2.3 验证阶段
```powershell
# 后端测试
cd backend && pytest --ignore=tests/tasks

# 前端测试（如涉及前端改动）
cd frontend && npm run test
```

### 2.4 提交阶段
- 遵循 Conventional Commits：`feat:` / `fix:` / `test:` / `refactor:` / `docs:` / `chore:`
- 提交前逐项检查 `docs/testing/review-checklist.md`
- 如果更新了 docs/testing/ 下的 Markdown 用例清单，运行 python automation/scripts/generate_excel_report.py 同步 Excel 报告

---

## 三、测试操作速查

| 操作 | 命令 | 参考文档 |
|------|------|----------|
| 运行后端全部 | `cd backend && pytest --ignore=tests/tasks` | `quick-reference.md` |
| 运行后端指定文件 | `cd backend && pytest tests/integrations/test_mapper.py -v` | `quick-reference.md` |
| 运行后端 + Allure | `cd backend && pytest --alluredir=./allure-results --ignore=tests/tasks` | `allure-report.md` |
| 生成 Allure 报告 | `allure generate ./allure-results -o ./allure-report --clean` | `allure-report.md` |
| 运行前端 | `cd frontend && npm run test` | `quick-reference.md` |
| 前端覆盖率 | `cd frontend && npm run test:coverage` | `quick-reference.md` |
| 生成测试报告 | python automation/scripts/generate_excel_report.py | generate_excel_report.py |

---

## 四、文档维护规则

| 变更场景 | 需更新的文档 |
|----------|-------------|
| 新增测试模式或工具 | `docs/testing/utilities.md` |
| 新增测试类型 | `docs/testing/directory-structure.md` |
| 调整命名规范 | `docs/testing/naming-conventions.md` |
| 新增 Mock 策略 | `docs/testing/fixture-and-mock.md` |
| 新增故障排查经验 | `docs/troubleshooting.md` |
| 新增业务规则 | `docs/business_rules.md` |
| 新增测试命令 | `docs/testing/quick-reference.md` |
| 更新 Markdown 用例清单 | 运行 python automation/scripts/generate_excel_report.py 刷新 Excel |
| 本文流程变更 | `AGENT.md` |

---

## 五、参考文档索引

| 文档 | 用途 |
|------|------|
| **测试规范合集** | |
| `docs/testing/index.md` | 测试规范总览入口 |
| `docs/testing/directory-structure.md` | 三模块 tests 目录结构标准 |
| `docs/testing/naming-conventions.md` | 测试文件/函数/类命名规范 |
| `docs/testing/fixture-and-mock.md` | Fixture 定义与 Mock 策略 |
| `docs/testing/test-data.md` | 测试数据管理规范 |
| `docs/testing/utilities.md` | 公共测试工具函数集合 |
| `docs/testing/allure-report.md` | Allure 报告集成规范 |
| `docs/testing/review-checklist.md` | 代码 Review 清单 |
| `docs/testing/development-workflow.md` | 新增测试开发流程 |
| `docs/testing/quick-reference.md` | 命令行速查表 |
| **业务文档** | |
| `docs/project_architecture.md` | 项目架构说明 |
| `docs/business_rules.md` | 业务规则 |
| `docs/troubleshooting.md` | 常见问题排查 |
| `docs/PRD.md` | 产品需求文档 |
| `backend/CODEBASE_OVERVIEW.md` | 后端代码结构总览 |
| `backend/INTEGRATION_DESIGN.md` | 外部任务源集成设计 |
| `CONTRIBUTING.md` | 贡献指南 |
