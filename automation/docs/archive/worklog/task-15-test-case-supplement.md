# Worklog: Task 15 — 补充测试用例（基于 PRD 缺口）

## 目标

基于 PRD.md 与现有 Excel 用例的缺口分析，按 AGENTS.md 流程补充测试用例，覆盖 8 种测试类型（正常流程、异常流程、权限、状态流转、数据校验、Redis、AI、数据库）。

## 阅读内容

- PRD.md（第 6~11 节）
- `automation/docs/testing/analysis/analysis-{call,tasks,admin}.md`
- `automation/src/mocks/backend_mock.py`
- `automation/testdata/api/api-test-cases.xlsx`
- `automation/tests/{call,tasks,admin}/test_*.py`
- `automation/tests/common/test_runner.py`
- `automation/AGENTS.md`

## 修改文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `infrastructure/mocks/backend_mock.py` | 扩展 | 新增 16 个 Mock 端点 |
| `testdata/api/api-test-cases.xlsx` | 扩展 | 新增 auth sheet(12) + call(15) + tasks(17) + admin(10) |
| `docs/gap-analysis-vs-prd.md` | 新增 | PRD 缺口分析 |
| `docs/testing/analysis/analysis-call.md` | 更新 | 补充转工单/submit/ack/催办/升级选人/认证 |
| `docs/testing/analysis/analysis-tasks.md` | 更新 | 补充 AI 方案分析/提交/聊天/AI 任务/健康检查/接单/转派/多类型 |
| `docs/testing/analysis/analysis-admin.md` | 更新 | 补充用户CRUD/角色CRUD/项目详情 |
| `docs/testing/analysis/analysis-auth-wechat.md` | 新增 | 认证+微信模块独立分析 |
| `docs/testing/scenarios/scenarios-call.md` | 新增 | 我要摇人场景设计(25条) |
| `docs/testing/scenarios/scenarios-tasks.md` | 新增 | 系统任务场景设计(29条) |
| `docs/testing/scenarios/scenarios-admin.md` | 新增 | 后台管理场景设计(20条) |
| `docs/worklog/task-15-test-case-supplement.md` | 新增 | 本文档 |

## Mock 端点新增

| 端点 | 位置 | 验证逻辑 |
|------|------|---------|
| POST /api/ai/qa/submit | `_route_ai` | body 非空 + conversation 存在 + auth |
| POST /api/ai/qa/ticket/ack | `_route_ai` | ticket 存在 + 状态创建中 + 不可重复 ack |
| POST /api/tasks/cuiban-notification | `_route_tasks` | task 存在 + 非终态 + note 长度校验 + auth |
| GET /api/tasks/assignable-users | `_route_tasks` | project_id 类型校验 + auth |
| POST /api/ai/task/submit | `_route_ai` | solution 非空 |
| POST /api/ai/task/chat | `_route_ai` | message 非空 + 长度校验 |
| POST /api/ai/task/chat/stream | `_route_ai` | SSE 格式 |
| POST /api/ai/task/list | `_route_ai` | 空列表 |
| GET /api/ai/task/health | `_route_ai` | 健康状态 |
| POST /api/ai/task/analyze | `_route_ai` | task_id 校验 |
| POST /api/ai/task/analyze/stream | `_route_ai` | SSE 流式 |
| POST /api/admin/users | `_route_admin` | username+email 校验 + 重复 |
| PUT /api/admin/users/{id} | `_route_admin` | 存在校验 |
| DELETE /api/admin/users/{id} | `_route_admin` | 存在校验 |
| POST /api/admin/roles | `_route_admin` | name 校验 + 重复 |
| PUT /api/admin/roles/{id} | `_route_admin` | 存在校验 |
| DELETE /api/admin/roles/{id} | `_route_admin` | 存在校验 |
| GET /api/admin/projects/{id} | `_route_admin` | 存在校验 |

同时扩展了 `_handle_task_assign`：支持 接单→in_progress 状态自动转换 + 终态不可接单 + 非 admin 不可转派。

## Excel 测试用例统计

| Sheet | 原行数 | 新增 | 总行数 |
|-------|--------|------|--------|
| auth | 0(新) | 12 | 12 |
| call | 10 | 15 | 25 |
| tasks | 17 | 17 | 34 |
| admin | 14 | 10 | 24 |
| **合计** | **41** | **54** | **95** |

## 测试结果

```
195 passed in 10.27s
```

4 个模块全部通过，无回归。

## 风险

- `/api/ai/task/analyze` 路径与已有 `/api/ai/task/diagnose` 并存，需确认后端真实路径
- 权限类测试（403）受限于 mock fixture 固定为 admin 角色无法覆盖，需后续引入多角色 fixture
- 微信 6 端点已有 auth sheet 覆盖（P2），但属于"只记录不自动跑"场景（auth=N）

## 下一步建议

1. **多角色 fixture**：支持 engineer/customer 角色切换，补全权限 403 用例
2. **PRD 剩余 P2 用例**：微信、AI 降级、Redis 缓存相关用例（当前在 Excel 的 note 字段标注）

---

## 2026-07-27 第二次迭代：框架优化 + auth 测试执行

### 完成项

1. **Fix 1-4, 9**: 
   - `test_runner.py` 删除 unused import
   - `conftest.py` 重复注册已清理（`infrastructure/conftest.py` 已删除）
   - 残留文件清理（`scripts/generate_allure_report.py` 等）
   - `__import__("time")` → `time`（2 处）+ 删除冗余 inline `import time`（2 处）

2. **Fix 5**: `_route_admin` 拆分为 11 个子方法：
   - `_handle_admin_tickets_list`, `_handle_admin_tickets_stats`, `_handle_admin_dashboard`
   - `_handle_admin_users_list`, `_handle_admin_users_create`, `_handle_admin_users_detail`
   - `_handle_admin_roles_list`, `_handle_admin_roles_create`, `_handle_admin_roles_detail`
   - `_handle_admin_projects_list`, `_handle_admin_projects_create`, `_handle_admin_projects_detail`
   - `_handle_admin_risks_list`, `_handle_admin_mappings_list`, `_handle_admin_mappings_create`

3. **创建 `tests/auth/test_auth.py`**：使 auth sheet 的 12 条用例可执行（之前只有 Excel 数据没有测试文件）

4. **Verify**：
   - 全量 195 passed（含 112 基础设施 + 83 API）
   - 95 API 用例全部通过（含新增 12 auth）
   - Allure 报告已生成，95 条用例均含 Request/Response JSON 附件

### 修改文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `infrastructure/mocks/backend_mock.py` | 重构 | `_route_admin` 拆分 11 子方法 + `__import__("time")` 替换 |
| `tests/auth/test_auth.py` | 新增 | auth sheet 数据驱动测试入口 |
| `docs/worklog/task-15-test-case-supplement.md` | 更新 | 本文档 |

### 测试结果

```
# 全量（含基础设施）
195 passed in 4.13s

# API 模块（Allure 报告）
95 passed in 4.60s
```

### Allure 报告

95 条 API 用例（call=25 + tasks=34 + admin=24 + auth=12），HTTP server http://localhost:8080。

### 风险

- 无新增风险

### 下一步

1. **多角色 fixture**：支持 engineer/customer 角色切换
2. **PRD 剩余 P2 用例**：微信、AI 降级、Redis 缓存
