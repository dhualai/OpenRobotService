# 常用 Codex Prompt

> 本文收集 OpenRobotService 项目中常用的 Codex / AI Agent Prompt 模板，用于快速生成代码、测试、文档等。
> 每个 Prompt 基于当前项目的实际代码风格和技术栈，并注明适用场景。

---

## 一、单元测试生成

### 1.1 后端 pytest 测试

```
你是一个 Python 测试专家。请为以下函数生成 pytest 测试代码。

项目：OpenRobotService
测试框架：pytest 9.1+，pytest-asyncio，allure-pytest
测试目录：backend/tests/{模块名}/
测试文件：test_{功能名}.py
参考示例：backend/tests/integrations/test_mapper.py

要求：
1. 使用 pytest 原生断言
2. 使用 @pytest.mark.parametrize 覆盖边界值（空值、None、异常输入）
3. 异步测试使用 async def + @pytest.mark.asyncio
4. 不需要外部 DB 连接（conftest.py 已 mock app 模块）
5. 如需 mock，使用 unittest.mock 或 pytest monkeypatch
6. 添加 type hints

待测代码：
```python
{粘贴待测代码}
```

生成测试代码，包含：
- 正常路径测试
- 边界值测试（空/None/非法输入）
- 异常路径测试
```

### 1.2 前端 vitest 测试

```
你是一个 TypeScript 测试专家。请为以下代码生成 vitest 测试。

项目：OpenRobotService
测试框架：Vitest 3.2+ + @testing-library/react 16 + jsdom 26
测试目录：frontend/src/{模块名}/__tests__/
测试文件：{名称}.test.ts/tsx
参考示例：frontend/src/api/__tests__/client.test.ts

要求：
1. 使用 vitest 的 describe/it/expect（globals: true）
2. 组件测试优先使用 @testing-library 角色查询
3. Store 测试使用 useAuthStore.getState()
4. 用户交互使用 @testing-library/user-event
5. 异步操作使用 waitFor / findBy
6. TypeScript strict 模式

待测代码：
```typescript
{粘贴待测代码}
```
```

---

## 二、API 测试生成

```
请为以下 FastAPI 端点生成 pytest 集成测试。

项目：OpenRobotService
测试框架：pytest + httpx TestClient
路由前缀：/api/{模块名}/
鉴权方式：JWT Bearer token（Depends require_permission）
测试目录：backend/tests/{模块名}/
测试文件：test_{端点名}_api.py
参考示例：backend/tests/tasks/test_standard_task_creation_api.py

要求：
1. 使用 httpx.AsyncClient 发送请求
2. Mock 外部依赖（使用 unittest.mock.patch）
3. 测试正常路径、参数校验错误、权限不足、资源不存在
4. 异步使用 async def + @pytest.mark.asyncio

端点代码：
```python
{粘贴路由代码}
```
```

---

## 三、新功能开发

### 3.1 后端新 API 端点

```
请在 OpenRobotService 后端（FastAPI）中实现以下功能。

项目路径：backend/app/modules/{模块名}/
参考风格：
- 路由：backend/app/modules/tasks/api/task.py（路径参数、请求体、响应模型）
- 服务层：backend/app/modules/tasks/services/ticket_service.py（CRUD 模式）
- 模型：backend/app/models/task.py（SQLAlchemy 模型）

技术约束：
- FastAPI 0.111+
- SQLAlchemy 2.0 async 模式
- Pydantic v2 模型
- 鉴权：require_permission() + get_async_db()
- 响应格式：统一 code/data/message 结构

功能需求：
{描述功能}

请生成：
1. API 路由（api/xxx.py）
2. Pydantic Schema（schemas/xxx.py）
3. Service 层（services/xxx.py）
4. 如果涉及新数据表：SQLAlchemy 模型
5. 对应的 pytest 测试
```

### 3.2 前端新页面

```
请在 OpenRobotService 前端中实现以下页面。

项目路径：frontend/src/pages/{模块名}/
参考风格：
- 组件：frontend/src/shared/components/ChatPanel.tsx（状态管理、事件处理）
- 页面：frontend/src/pages/call/CallView.tsx（布局、组件组合）
- Store：frontend/src/stores/workbench.ts（全局状态）

技术约束：
- React 19 + TypeScript 5.9（strict）
- TDesign Mobile React 0.23
- React Router v7
- Zustand 5 状态管理
- 移动端优先（H5，微信内打开）
- 懒加载：React.lazy + <Suspense>

功能需求：
{描述功能}

请生成：
1. 页面组件（xxx.tsx）
2. 如果涉及 API：api/xxx.ts 客户端
3. 如果涉及全局状态：store 更新
4. 路由配置更新
5. 对应的 vitest 测试
```

---

## 四、测试问题修复

### 4.1 pytest 收集失败

```
pytest 在收集测试时失败，错误信息：
{paste error}

项目结构：
- backend/app/ 下的 __init__.py 在 import 时连 MySQL（技术债）
- conftest.py 使用占位模块阻止 DB 连接
- 错误文件：{文件路径}

请分析原因并提供修复方案。
注意：不要修改业务代码，只修改测试代码或 conftest.py。
```

### 4.2 vitest 测试失败

```
vitest 测试失败，错误信息：
{paste error}

项目结构：
- Vite 7 + React 19
- jsdom 环境
- setupFiles: ./src/test/setup.ts（含 localStorage/matchMedia mock）

请分析原因并提供修复方案。
```

---

## 五、代码审查

### 5.1 测试覆盖率分析

```
请分析以下代码的测试覆盖缺口：

```python
{paste code}
```

1. 有哪些分支尚未覆盖？
2. 有哪些边界值需要补充测试？
3. 是否有外部依赖需要 mock？
4. 是否需添加集成测试？

请给出具体的测试用例清单。
```

---

## 六、批量测试生成

### 6.1 为整个模块生成测试基线

```
请为 OpenRobotService 的 {模块名} 模块生成完整的测试基线。

项目信息：
- 后端：Python/FastAPI + pytest
- 前端：React/TypeScript + vitest
- 测试规范：docs/testing_guidelines.md
- 测试策略：docs/automation_strategy.md

模块路径：backend/app/modules/{模块名}/
模块文件清单：
{文件列表}

请为每个文件生成：
1. 测试文件路径建议
2. 核心测试用例列表（含输入输出）
3. 需要 mock 的外部依赖
4. 需要前置条件（如数据库数据）
```

---

## 七、相关文档

| 文档 | 路径 |
|------|------|
| 测试开发规范 | `docs/testing_guidelines.md` |
| 自动化测试方案 | `docs/automation_strategy.md` |
| Code Review 清单 | `docs/review_checklist.md` |
| 功能完成标准 | `docs/done_definition.md` |
| 项目架构说明 | `docs/project_architecture.md` |
| 业务规则 | `docs/business_rules.md` |
