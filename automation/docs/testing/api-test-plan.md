# API 自动化测试计划

## 总体架构

`
API_V1_STR = /api

/api/auth/           → 认证（登录/注册/刷新/OAuth）
/api/tasks/          → 工单（CRUD + 状态流转 + 评论 + AI分配+ 附件）
/api/conversations/  → 会话（摇人）
/api/qa/             → QA（含流式 SSE）
/api/messages/       → 消息
/api/my-tasks/       → 我的工单
/api/admin/*         → 后台管理（工单/项目/风险/日报/用户/角色/权限）
/api/wechat/*        → 微信（菜单/消息/标签/通知/健康检查）
/api/integrations/   → 外部任务源
/api/health          → 健康检查
`

## 分阶段计划

### Phase 0 — Smoke + Auth（10 用例，P0）

| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 1 | Health | GET /api/health | 健康检查返回 200 | P0 |
| 2 | Health | GET /api/health | 响应结构校验 | P0 |
| 3 | Auth | POST /api/auth/login | 正常登录 → JWT | P0 |
| 4 | Auth | POST /api/auth/login | 错误密码 → 401 | P0 |
| 5 | Auth | POST /api/auth/login | 不存在用户 → 401 | P0 |
| 6 | Auth | POST /api/auth/login | 空字段 → 422 | P1 |
| 7 | Auth | POST /api/auth/login | 缺字段 → 422 | P1 |
| 8 | Auth | GET /api/auth/me | 有效 Token → 用户信息 | P0 |
| 9 | Auth | GET /api/auth/me | 无 Token → 401 | P1 |
| 10 | Auth | GET /api/auth/me | 伪造 Token → 401 | P1 |

**状态**：✅ 已实现并提交（3d7f6ed）

### Phase 1 — Tasks CRUD + 状态流转（16 用例）

| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 11 | Tasks | POST /api/tasks/ | 创建工单（必填字段）| P0 |
| 12 | Tasks | POST /api/tasks/ | 创建工单（全字段）| P0 |
| 13 | Tasks | POST /api/tasks/ | 缺少必填字段 → 422 | P1 |
| 14 | Tasks | GET /api/tasks/ | 获取工单列表 | P0 |
| 15 | Tasks | GET /api/tasks/ | 分页参数测试 | P1 |
| 16 | Tasks | GET /api/tasks/{id} | 获取工单详情 | P0 |
| 17 | Tasks | GET /api/tasks/{id} | 工单不存在 → 404 | P1 |
| 18 | Tasks | PUT /api/tasks/{id} | 更新工单 | P0 |
| 19 | Tasks | PATCH /api/tasks/{id}/status | 变更状态（合法流转）| P0 |
| 20 | Tasks | PATCH /api/tasks/{id}/status | 变更状态（非法流转）→ 400 | P1 |
| 21 | Tasks | PATCH /api/tasks/{id}/assign | 分配处理人 | P0 |
| 22 | Tasks | DELETE /api/tasks/{id} | 删除工单 | P1 |
| 23 | Tasks | POST /api/tasks/filter | 多条件筛选 | P1 |
| 24 | Tasks | GET /api/tasks/stats/overview | 工单统计 | P1 |
| 25-26 | Tasks | POST+GET /api/tasks/{id}/comments | 评论 CRUD | P1 |
| 27 | Tasks | POST /api/tasks/{id}/ai-assign | AI 自动分配 | P2 |
| 28 | Tasks | POST /api/tasks/{id}/comments/attachments | 附件上传 | P2 |

**状态**：✅ 已完成

### Phase 2 — WeChat（6 用例）

**状态**：✅ 已完成（4825635）

| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 29 | WeChat | GET /api/wechat/health | 健康检查 | P1 |
| 30 | WeChat | GET /api/wechat/menu | 获取菜单 | P1 |
| 31 | WeChat | POST /api/wechat/menu | 创建/更新菜单 | P2 |
| 32 | WeChat | POST /api/wechat/message | 发送消息 | P2 |
| 33 | WeChat | GET /api/wechat/tag | 标签管理 | P2 |
| 34 | WeChat | POST /api/wechat/notify | 模板通知 | P2 |

### Phase 3 — Call + QA + Stream（10 用例）

**状态**：✅ 已完成（f4f89ed）

| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 35 | Call | POST /api/conversations/ | 创建会话 | P0 |
| 36 | Call | GET /api/conversations/ | 会话列表 | P0 |
| 37 | Call | GET /api/conversations/{id} | 会话详情+消息 | P0 |
| 38 | Call | POST /api/qa/ | QA 问答 | P1 |
| 39 | Call | POST /api/qa/stream | 流式 SSE 问答 | P0 |
| 40 | Call | GET /api/messages/ | 消息列表 | P2 |
| 41 | Call | GET /api/my-tasks/ | 我的工单 | P2 |

### Phase 4 — Admin（8 用例）

**状态**：✅ 已完成（b094068）

| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 42 | Admin | GET /api/admin/tickets | 后台工单列表 | P1 |
| 43 | Admin | GET /api/admin/tickets/stats | 工单统计 | P1 |
| 44 | Admin | GET /api/admin/projects | 项目列表 | P2 |
| 45 | Admin | POST /api/admin/projects | 创建项目 | P2 |
| 46 | Admin | GET /api/admin/risks | 风险列表 | P2 |
| 47 | Admin | GET /api/admin/dashboard | 仪表盘 | P2 |
| 48 | Admin | GET /api/admin/users | 用户管理 | P2 |
| 49 | Admin | GET /api/admin/roles | 角色列表 | P2 |

### Phase 5 — Integrations（2 用例）

**状态**：✅ 已完成（082ff3d）
### Phase 4.5 — Admin Extensions（6 用例）

| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 50 | Admin | POST /api/admin/daily-reports | 创建日报 | P1 |
| 51 | Admin | POST /api/admin/daily-reports | 创建周报 | P1 |
| 52 | Admin | POST /api/admin/export | 数据导出 | P2 |
| 53 | Admin | GET /api/admin/resources | 资源列表 | P1 |
| 54 | Admin | POST /api/admin/resources | 创建资源 | P1 |
| 55 | Admin | GET /api/admin/resources/{id} | 资源详情 | P1 |
| 56 | Admin | PUT /api/admin/resources/{id} | 更新资源 | P1 |

**状态**：✅ 已完成（10e10f9）

### Phase 6 — AI Backend Endpoints（3 用例）

| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 57 | AI | POST /api/ai/task/diagnose | AI 诊断工单 | P1 |
| 58 | AI | POST /api/ai/task/discuss | AI 讨论工单 | P1 |
| 59 | AI | POST /api/ai/task/summarize | AI 摘要生成 | P1 |

**状态**：✅ 已完成（10e10f9）



| # | 模块 | 接口 | 测试点 | 优先级 |
|---|------|------|--------|--------|
| 50 | Integ | GET /api/integrations | 外部源列表 | P2 |
| 51 | Integ | POST /api/admin/integrations/mappings | 映射管理 | P2 |
| 52 | AI | POST /api/ai/task/diagnose | AI 诊断 | P2 |

## 测试前置依赖

| 依赖 | 状态 | 说明 |
|------|------|------|
| auth_token fixture | ✅ | conftest.py 已实现 |
| auth_header fixture | ✅ | conftest.py 已实现 |
| mock backend | ✅ | mocks/backend_mock.py |
| mock_api_client fixture | ✅ | conftest.py |
| suite fixture | ✅ | e2e/conftest.py |

## 优先级分布

| 级别 | 数量 | 占比 |
|------|------|------|
| P0 | 14 | 27% |
| P1 | 18 | 35% |
| P2 | 20 | 38% |
| **总计** | **52** | |

## 实施路线

`
Phase 0 (Auth+Health)  →  ✅ 已提交
Phase 1 (Tasks)         →  ✅ 已提交（f4f89ed）
Phase 2 (WeChat)        →  ✅ 已提交（4825635）
Phase 3 (Call+QA+Stream) →  ✅ 已提交（f4f89ed）
Phase 4 (Admin)         →  ✅ 已提交（b094068）
Phase 5 (Integrations)  →  ✅ 已提交（082ff3d）
Phase 4.5 (Admin Extensions) →  ✅ 已提交（10e10f9）
Phase 6 (AI Endpoints)       →  ✅ 已提交（10e10f9）

CI 搭建                →  Phase 1-2 完成后
`

