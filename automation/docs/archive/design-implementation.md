# 第四阶段设计：补充测试用例

> 设计确认文档 — 基于分析缺口和场景设计，制定实现计划
> 日期：2026-07-27

---

## 1. 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `infrastructure/mocks/backend_mock.py` | 扩展 | 新增 10 个 Mock 端点 |
| `testdata/cases/api-test-cases.xlsx` | 扩展 | 新增 3 sheet（auth）+ 补充现有 3 sheet |
| `tests/call/test_call.py` | 不改 | `load_cases` + `parametrize` 自动加载新数据 |
| `tests/tasks/test_tasks.py` | 不改 | 同上 |
| `tests/admin/test_admin.py` | 不改 | 同上 |

---

## 2. 模块职责划分

| 模块 | Excel Sheet | 新增用例数 | 新增 Mock 端点 |
|------|------------|-----------|---------------|
| 认证 + 微信 | `auth`（新 sheet） | 12 | 0（mock 已有） |
| 我要摇人 | `call`（补充现有） | 15 | 4 |
| 系统任务 | `tasks`（补充现有） | 18 | 5 |
| 后台管理 | `admin`（补充现有） | 10 | 6 |
| **合计** | | **55** | **15** |

---

## 3. Excel 变更

### 3.1 `auth` 新 sheet（认证 + 微信，12 条）

| 用例ID | 功能 | 优先级 | 新? |
|--------|------|--------|-----|
| AUTH-001 | 登录成功 | P0 | ✅ |
| AUTH-002 | 密码错误 | P0 | ✅ |
| AUTH-003 | 空密码 | P0 | ✅ |
| AUTH-004 | 用户名不存在 | P0 | ✅ |
| AUTH-005 | 获取当前用户成功 | P0 | ✅ |
| AUTH-006 | 无效 token | P0 | ✅ |
| AUTH-007 | token 过期 | P0 | ✅ |
| AUTH-008 | 微信健康检查 | P2 | ✅ |
| AUTH-009 | 获取微信菜单 | P2 | ✅ |
| AUTH-010 | 创建微信菜单 | P2 | ✅ |
| AUTH-011 | 发送微信消息 | P2 | ✅ |
| AUTH-012 | 微信回调 | P2 | ✅ |

### 3.2 `call` 补充（15 条）

| 用例ID | 功能 | 优先级 | 新? |
|--------|------|--------|-----|
| CALL-006 | 转工单 | P0 | ✅ |
| CALL-007 | 转工单-body空 | P0 | ✅ |
| CALL-008 | 转工单-会话不存在 | P0 | ✅ |
| CALL-009 | 转工单-无token | P0 | ✅ |
| CALL-010 | 确认派单 | P0 | ✅ |
| CALL-011 | 确认派单-ticket不存在 | P0 | ✅ |
| CALL-012 | 确认派单-重复 | P0 | ✅ |
| CALL-013 | 催办 | P0 | ✅ |
| CALL-014 | 催办-task不存在 | P0 | ✅ |
| CALL-015 | 催办-无token | P0 | ✅ |
| CALL-016 | 催办-备注超长 | P1 | ✅ |
| CALL-017 | 催办-已closed | P1 | ✅ |
| CALL-018 | 升级选人 | P0 | ✅ |
| CALL-019 | 升级选人-project非法 | P0 | ✅ |
| CALL-020 | 升级选人-无token | P0 | ✅ |
| CALL-021 | 升级选人-空列表 | P1 | ✅ |

### 3.3 `tasks` 补充（18 条）

| 用例ID | 功能 | 优先级 | 新? |
|--------|------|--------|-----|
| TASK-012 | AI 方案分析 | P1 | ✅ |
| TASK-013 | AI 分析-task不存在 | P1 | ✅ |
| TASK-014 | AI 方案提交 | P1 | ✅ |
| TASK-015 | AI 提交-空方案 | P1 | ✅ |
| TASK-016 | AI 聊天 | P1 | ✅ |
| TASK-017 | AI 聊天流式 | P1 | ✅ |
| TASK-018 | AI 任务列表 | P1 | ✅ |
| TASK-019 | AI 健康检查 | P1 | ✅ |
| TASK-020 | 工程师接单 | P0 | ✅ |
| TASK-021 | 接单-已分配 | P0 | ✅ |
| TASK-022 | 接单-cancelled不可 | P0 | ✅ |
| TASK-023 | admin转派 | P0 | ✅ |
| TASK-024 | 非admin转派 | P0 | ✅ |
| TASK-025 | 多任务type=bug | P1 | ✅ |
| TASK-026 | 多任务type=requirement | P1 | ✅ |
| TASK-027 | 多任务type=support | P1 | ✅ |
| TASK-028 | 多任务type非法 | P1 | ✅ |
| TASK-029 | closed不可重开 | P0 | ✅ |

### 3.4 `admin` 补充（10 条）

| 用例ID | 功能 | 优先级 | 新? |
|--------|------|--------|-----|
| ADMIN-013 | 创建用户 | P1 | ✅ |
| ADMIN-014 | 更新用户 | P1 | ✅ |
| ADMIN-015 | 删除用户 | P1 | ✅ |
| ADMIN-016 | 创建角色 | P1 | ✅ |
| ADMIN-017 | 更新角色 | P1 | ✅ |
| ADMIN-018 | 删除角色 | P1 | ✅ |
| ADMIN-019 | 项目详情 | P1 | ✅ |
| ADMIN-020 | 用户不存在 | P1 | ✅ |
| ADMIN-021 | 角色名称重复 | P1 | ✅ |
| ADMIN-022 | 非admin 403 | P0 | ✅ |

---

## 4. Mock 扩展

在 `MockBackend` 中新增以下路由：

### Phase 1 — 催办与选人（3 端点）

| 路径 | 方法 | 行为 |
|------|------|------|
| `/api/tasks/cuiban-notification` | POST | 校验 task_id → 校验工单状态(not closed) → 返回 200/{success} |
| `/api/tasks/assignable-users` | GET | 校验 project_id 类型 → 返回 200/[users] 或 400 |

### Phase 2 — 转工单链路（2 端点）

| 路径 | 方法 | 行为 |
|------|------|------|
| `/api/ai/qa/submit` | POST | 校验 body 非空 → 返回 200/{ticket_id, status: created} |
| `/api/ai/qa/ticket/ack` | POST | 校验 ticket_id → 校验状态(不可重复ack) → 200/{status: acknowledged} |

### Phase 3 — AI 模块（5 端点）

| 路径 | 方法 | 行为 |
|------|------|------|
| `/api/ai/task/submit` | POST | 校验 solution 非空 → 200 |
| `/api/ai/task/chat` | POST | 校验 message → 200/{reply} |
| `/api/ai/task/chat/stream` | POST | SSE 流式返回 |
| `/api/ai/task/list` | POST | 返回 200/[tasks] |
| `/api/ai/task/health` | GET | 返回 200/{status: ok} |

### Phase 4 — 管理后台 CRUD（6 端点）

| 路径 | 方法 | 行为 |
|------|------|------|
| `/api/admin/users` | POST | 校验 username 非空 + 邮箱格式 → 201 |
| `/api/admin/users/{id}` | PUT | 校验 id 存在 → 200 |
| `/api/admin/users/{id}` | DELETE | 校验 id 存在 → 204 |
| `/api/admin/roles` | POST | 校验 name 非空 + 不重复 → 201 |
| `/api/admin/roles/{id}` | PUT | 校验 id 存在 → 200 |
| `/api/admin/roles/{id}` | DELETE | 校验 id 存在 → 204 |
| `/api/admin/projects/{id}` | GET | 校验 id 存在 → 200，不存在 → 404 |

---

## 5. 实现顺序（按 Phase）

```
Phase 1: Mock 催办/选人 → Excel auth sheet → pytest auth
Phase 2: Mock 转工单/submit/ack → Excel call 补充 → pytest call
Phase 3: Mock AI 5 端点 → Excel tasks 补充 → pytest tasks
Phase 4: Mock admin CRUD 6 端点 → Excel admin 补充 → pytest admin
Phase 5: Allure 报告 + worklog
```

每个 Phase 写完 Excel 行后，不修改测试代码，直接 `pytest tests/{module}/ -v` 验证。

---

## 6. 风险分析

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Excel 新增 sheet 后框架报错 | 自动化运行失败 | 验证 `load_cases` 支持多 sheet |
| Mock 端点行为与 Excel 预期不一致 | 测试误判 | 每新增一个端点立即跑 pytest |
| 原测试用例因新数据位移行号 | 维护性 | Excel 是数据驱动，行号不影响，按 id 匹配 |
| 一次性修改 >10 文件 | 违反规范 | 分为 4 Phase，每 Phase <10 文件 |
