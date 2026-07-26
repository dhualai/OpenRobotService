# 自动化代码 Review 清单

> 本文是 AI Agent 在提交代码前进行自检的检查清单，也适用于人工 Code Review。
> 基于当前项目实际的技术栈和架构设计。

---

## 一、通用检查

- [ ] **无敏感信息**：代码中未硬编码密码、Token、API Key、数据库连接串
- [ ] **无 TODO / FIXME / DEBUG 残留**：提交前清理调试代码
- [ ] **日志级别正确**：生产环境不打 `print()` 或 `logging.debug` 级敏感信息
- [ ] **异常处理完整**：外部调用（DB/API/文件）有 try-except 或错误传播
- [ ] **类型注解完整**：Python 函数有参数/返回类型注解；TypeScript 有完整类型定义

---

## 二、后端检查（Python / FastAPI / pytest）

### 2.1 代码规范

- [ ] **遵循 PEP 8**：ruff 格式化通过（`cd backend && ruff check app/`）
- [ ] **导入顺序正确**：标准库 → 第三方 → 本地模块
- [ ] **无循环导入**：`app/core/database.py` 等基础模块不反向 import 业务模块
- [ ] **异步用法正确**：`async def` 函数内部使用 `await`，同步/异步不混用

### 2.2 路由规范

- [ ] **路由前缀正确**：`/api/` 开头，遵循模块前缀约定（auth/admin/tasks/call/wechat）
- [ ] **鉴权完整**：敏感接口使用 `Depends(require_permission(...))`
- [ ] **路由注册顺序**：集成源路由（`/tasks/sources`）在任务路由（`/tasks/{task_id}`）之前注册
- [ ] **响应格式一致**：统一使用 `JSONResponse` 或 Pydantic 响应模型

### 2.3 测试规范

- [ ] **新增逻辑有对应测试**：映射函数、状态机、API 端点
- [ ] **测试不依赖外部服务**：单元测试使用 conftest.py 的 mock 机制
- [ ] **参数化覆盖边界**：空值、None、异常输入、边界值
- [ ] **测试可重复执行**：无状态残留、无顺序依赖
- [ ] **集成测试有条件控制**：需要外部服务的测试用 `@pytest.mark.skipif`

---

## 三、前端检查（React / TypeScript / vitest）

### 3.1 代码规范

- [ ] **TypeScript 编译通过**：`cd frontend && npx tsc -b` 无 error
- [ ] **ESLint 无 error**：`cd frontend && npm run lint` 退出码 0
- [ ] **无 `any` 类型**（mock 组件除外）：`@typescript-eslint/no-explicit-any` 为 error
- [ ] **路径别名正确**：`@/` → `src/`，不使用相对路径 `../../`
- [ ] **组件导出风格统一**：`export default function ComponentName`

### 3.2 路由规范

- [ ] **新页面放在正确模块下**：`pages/call/` / `pages/tasks/` / `pages/admin/`
- [ ] **懒加载**：页面组件使用 `React.lazy` + `<Suspense>`
- [ ] **返回按钮**：页面左上角 `<Navbar leftArrow onLeftClick={() => navigate(-1)} />`

### 3.3 测试规范

- [ ] **组件测试优先使用 Testing Library 角色查询**：`getByRole` / `findByText`
- [ ] **Store 测试隔离**：每个测试前 reset store 状态
- [ ] **异步操作使用 waitFor / findBy**：等待 DOM 更新后再断言
- [ ] **用户交互使用 user-event**：`@testing-library/user-event` 而非 `fireEvent`

---

## 四、AI 模块检查（Python / Agent）

- [ ] **LLM API 调用有超时和重试**：防止网络故障导致服务不可用
- [ ] **Prompt 不可硬编码在代码中**：放配置文件或独立 prompt 文件
- [ ] **流式响应（SSE）正确处理**：`async for` 逐块处理，异常时优雅降级
- [ ] **知识库检索有兜底**：向量检索无结果时回退关键字搜索

---

## 五、测试框架配置检查

- [ ] **后端 `requirements-test.txt` 已添加新测试依赖**（如适用）
- [ ] **前端 `package.json` 已添加新测试依赖**（如适用）
- [ ] **Allure 装饰器正确使用**（如适用）：`@allure.feature` / `@allure.story`
- [ ] **pytest.ini 更新**（如适用）：新增 markers、addopts 等

---

## 六、性能与安全

- [ ] **N+1 查询**：ORM 查询确认未触发 N+1（使用 `joinedload` / `selectinload`）
- [ ] **密码/敏感字段不在日志中输出**
- [ ] **用户输入做校验/清洗**：XSS 防护（后端 `python-multipart` / 前端 `dompurify`）
- [ ] **缓存 key 包含用户维度**：避免跨用户缓存污染

---

## 七、提交前检查

- [ ] `git status` 确认只包含本次改动的文件
- [ ] 提交信息符合 Conventional Commits 格式
- [ ] 运行后端测试：`cd backend && pytest --ignore=tests/tasks`
- [ ] 运行前端测试（如涉及前端改动）：`cd frontend && npm run test`
- [ ] 相关文档已同步更新（`docs/` 下对应文件）

---

## 八、相关文档

| 文档 | 路径 |
|------|------|
| 测试开发规范 | `docs/testing_guidelines.md` |
| 自动化测试方案 | `docs/automation_strategy.md` |
| 功能完成标准 | `docs/done_definition.md` |
| 项目架构说明 | `docs/project_architecture.md` |
| 业务规则 | `docs/business_rules.md` |
