# 自动化测试方案

> 本文定义 OpenRobotService 项目的自动化测试策略、分层模型、CI 集成方式和报告体系。
> 测试开发规范见 `docs/testing_guidelines.md`，报告模板见 `docs/test_report_guideline.md`。

---

## 一、测试金字塔

```
            ╱─────╲
           ╱  E2E  ╲          ← 集成端到端测试（需真实 DB/外部服务）
          ╱─────────╲
         ╱  集成测试  ╲        ← API 测试、数据库集成测试
        ╱─────────────╲
       ╱   单元测试     ╲      ← 函数/方法/组件级测试（无外部依赖）
      ╱─────────────────╲
```

### 当前覆盖状态

| 层级 | 覆盖内容 | 测试文件 |
|------|----------|----------|
| **单元测试** | 状态映射、优先级映射、任务类型映射 | `tests/integrations/test_mapper.py` |
| **单元测试** | 项目 ID 解析、配置检查 | `tests/integrations/test_adapter.py` |
| **单元测试** | 状态合并逻辑 | `tests/integrations/test_engine.py` |
| **单元测试** | API 客户端（Token/缓存/重试） | `frontend/src/api/__tests__/client.test.ts` |
| **单元测试** | 前端状态管理（Auth/Workbench） | `frontend/src/stores/__tests__/` |
| **单元测试** | 前端工具函数 | `frontend/src/shared/utils/__tests__/` |
| **集成测试** | API 级标准建单（mock 服务） | `tests/tasks/test_standard_task_creation_api.py` |
| **集成测试** | MySQL 持久化（需 TEST_DATABASE_URL） | `tests/tasks/test_standard_task_creation_db.py` |
| **AI 测试** | LLM API 连通性 | `ai/tests/test_llm_api.py` |

---

## 二、测试分层详细说明

### 2.1 单元测试（Unit Tests）

**目标**：验证单一函数/方法/组件的正确性，无外部依赖。

| 测试内容 | 技术 | 位置 |
|----------|------|------|
| 映射函数 | pytest + parametrize | `tests/integrations/test_mapper.py` |
| 状态合并 | pytest + parametrize | `tests/integrations/test_engine.py` |
| 配置检查 | pytest | `tests/integrations/test_adapter.py` |
| API 客户端 | vitest | `frontend/src/api/__tests__/client.test.ts` |
| Store 逻辑 | vitest | `frontend/src/stores/__tests__/` |
| 工具函数 | vitest | `frontend/src/shared/utils/__tests__/` |

**后端单元测试不依赖**：MySQL、Redis、MinIO、微信 API、LLM API。

### 2.2 集成测试（Integration Tests）

**目标**：验证模块间协作，包括 API 调用和数据持久化。

| 测试内容 | 技术 | 条件 |
|----------|------|------|
| 标准建单 API | pytest + httpx TestClient + mock | 无需外部服务 |
| 标准建单 DB | pytest + asyncmy | 需 `TEST_DATABASE_URL` 环境变量 |
| 外部任务源同步 | 待补充 | 需禅道实例 |

### 2.3 E2E 测试（待建设）

**目标**：验证完整用户旅程。

目前 E2E 测试尚未覆盖，建议未来引入 Playwright 或 Cypress。

---

## 三、测试数据管理

### 3.1 硬编码测试数据

适用于映射函数等纯逻辑测试，示例取自真实禅道数据：

```python
# tests/integrations/test_mapper.py
SAMPLE_ZENTAO_TASK = {
    "id": 123,
    "name": "【测试任务】验证派单流程",
    "desc": "功能描述：验证工单系统能否成功接收禅道同步的测试任务...",
    "status": "wait",
    "pri": 1,
    "type": "devel",
    "assignedTo": {"account": "zhangjunlei", "realname": "张俊磊"},
    "openedBy": {"account": "zhangsan", "realname": "张三"},
    "openedDate": "2026-07-14T10:00:00Z",
    "deadline": "2026-07-20",
    "estimate": 8,
    "consumed": 3.5,
    "left": 4.5,
}
```

### 3.2 动态测试数据

前端测试使用 `@testing-library/user-event` 模拟用户交互，不硬编码 UI 状态。

### 3.3 数据库测试数据

集成测试通过 `conftest.py` 或 factory 函数创建测试数据，避免依赖 fixture 文件。

---

## 四、测试执行策略

### 4.1 本地开发

```powershell
# 后端全部单元测试
cd backend
pytest --ignore=tests/tasks

# 后端特定文件
pytest tests/integrations/test_mapper.py -v

# 后端指定标记
pytest -m "not slow"

# 前端
cd frontend
npm run test
npm run test:watch    # 监视模式
```

### 4.2 Allure 报告

```powershell
# 带 Allure 执行（需安装 allure-pytest）
cd backend
pytest --alluredir=./allure-results --ignore=tests/tasks

# 生成 HTML 报告（需安装 Allure CLI + Java 17+）
allure generate ./allure-results -o ./allure-report --clean

# 打开报告
allure open ./allure-report
```

### 4.3 pytest-html 轻量方案

```powershell
cd backend
pip install pytest-html
pytest --html=report.html --self-contained-html --ignore=tests/tasks
```

---

## 五、CI/CD 集成（待建设）

当前项目未配置 CI workflows。推荐以下集成方案：

### 5.1 GitHub Actions 配置建议

```yaml
name: Tests
on: [push, pull_request]

jobs:
  backend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r backend/requirements-test.txt
      - run: pytest backend/ --ignore=backend/tests/tasks --alluredir=allure-results
      - uses: actions/upload-artifact@v4
        with:
          name: allure-results
          path: allure-results

  frontend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "18"
      - run: cd frontend && npm ci
      - run: cd frontend && npm run test
```

---

## 六、测试报告体系

| 报告类型 | 工具 | 内容 | 受众 |
|----------|------|------|------|
| Allure 报告 | allure-pytest + Allure CLI | 测试结果趋势、分类、步骤、附件 | 开发/测试 |
| pytest-html | pytest-html | 单次测试结果 HTML | 开发 |
| 覆盖率报告 | pytest-cov / @vitest/coverage-v8 | 代码覆盖率 | 开发 |
| 前端 vitest 报告 | vitest | 测试结果 JSON | CI |

---

## 七、当前覆盖缺口（TODO）

| 模块 | 缺口 | 优先级 | 建议方案 |
|------|------|--------|----------|
| `app/modules/call/` | 无自动化测试 | P1 | 对话 API 集成测试 |
| `app/modules/admin/` | 无自动化测试 | P1 | 管理 API CRUD 测试 |
| `app/wechat/` | 无自动化测试 | P2 | 微信回调/通知测试 |
| `ai/agents/` | 无自动化测试 | P2 | Agent prompt 回归测试 |
| `ai/ingestion/` | 无自动化测试 | P2 | 知识摄取流水线测试 |
| E2E 流程 | 无覆盖 | P2 | Playwright 用户旅程测试 |

---

## 八、相关文档

| 文档 | 路径 |
|------|------|
| 测试开发规范 | `docs/testing_guidelines.md` |
| 测试报告规范 | `docs/test_report_guideline.md` |
| Code Review 检查清单 | `docs/review_checklist.md` |
| 项目架构说明 | `docs/project_architecture.md` |
| 业务规则 | `docs/business_rules.md` |
| 常见问题排查 | `docs/troubleshooting.md` |
