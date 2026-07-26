# API 测试用例清单

> 格式按 [template-test-case.md](template-test-case.md)

---

## Phase 0 - Auth + Health

### API-TC-001 — test_health_check

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 健康检查

**测试点：** 验证服务健康检查接口正常返回

**前置条件：** 无（无需登录）

**测试步骤：**
1. 发送 GET /api/health → 200，body 包含 status/message/service 字段

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-002 — test_health_response_structure

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 健康检查响应结构

**测试点：** 验证健康检查响应结构完整性

**前置条件：** 无（无需登录）

**测试步骤：**
1. 发送 GET /api/health → 验证响应包含 status/message/service 字段

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-003 — test_login_success

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 登录成功

**测试点：** 使用正确凭据登录获取 JWT Token

**前置条件：** 系统中存在 testadmin 用户

**测试步骤：**
1. 填写 username=testadmin, password=admin123
2. 发送 POST /auth/login → 200，返回 access_token

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-004 — test_login_wrong_password

**属性：** 优先级 P0 · 自动化 · 冒烟 否 · 功能点 登录密码错误

**测试点：** 使用错误密码登录验证返回 401

**前置条件：** 系统中存在 testadmin 用户

**测试步骤：**
1. 填写 username=testadmin, password=wrong
2. 发送 POST /auth/login → 401

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-005 — test_login_user_not_found

**属性：** 优先级 P0 · 自动化 · 冒烟 否 · 功能点 用户不存在

**测试点：** 使用不存在用户名登录验证返回 401

**前置条件：** 用户 notexist 不存在

**测试步骤：**
1. 填写 username=notexist
2. 发送 POST /auth/login → 401

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-006 — test_login_empty_username

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 用户名为空

**测试点：** 提交空用户名验证返回 422

**前置条件：** 无

**测试步骤：**
1. username=空，发送 POST /auth/login → 422

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-007 — test_login_missing_fields

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 缺少必填字段

**测试点：** 不传 username 验证返回 422

**前置条件：** 无

**测试步骤：**
1. 发送 POST /auth/login 不带 username → 422

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-008 — test_get_current_user_with_valid_token

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 有效Token获取用户

**测试点：** 携带有效 JWT 获取当前用户信息

**前置条件：** 已完成登录获取到有效 Token

**测试步骤：**
1. Header: Authorization: Bearer <token>
2. 发送 GET /auth/me → 200，返回用户信息

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-009 — test_get_current_user_no_token

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 无Token获取用户

**测试点：** 不携带 Token 获取用户验证 401

**前置条件：** 无

**测试步骤：**
1. 不携带 Authorization Header
2. 发送 GET /auth/me → 401

**结果：** PASS

---

## Phase 0 - Auth + Health

### API-TC-010 — test_get_current_user_forged_token

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 伪造Token

**测试点：** 携带伪造 JWT 验证返回 401

**前置条件：** 无

**测试步骤：**
1. Header: Bearer fake-jwt-token
2. 发送 GET /auth/me → 401

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-011 — test_create_task_minimal

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 创建工单-最小字段

**测试点：** 使用必填字段创建工单

**前置条件：** 已登录持有有效 Token

**测试步骤：**
1. 填写 title="Test task"
2. 发送 POST /api/tasks → 200，返回 task 对象含 id/title/status/created_at

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-012 — test_create_task_full_fields

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 创建工单-全字段

**测试点：** 填写全部字段创建工单

**前置条件：** 已登录；存在 engineer 用户

**测试步骤：**
1. 填写 title/description/priority/assignee_id/tags
2. 发送 POST /api/tasks → 200，返回完整 task 对象

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-013 — test_create_task_missing_title

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 创建工单-缺标题

**测试点：** 不传 title 验证返回 422

**前置条件：** 已登录

**测试步骤：**
1. 不传 title
2. 发送 POST /api/tasks → 422

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-014 — test_task_list

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 工单列表

**测试点：** 获取工单列表

**前置条件：** 已登录；系统中有至少 1 条工单

**测试步骤：**
1. 发送 GET /api/tasks → 200，返回 items 数组和 total 总数

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-015 — test_task_list_pagination

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 工单列表分页

**测试点：** 验证分页参数

**前置条件：** 系统中有至少 5 条工单

**测试步骤：**
1. GET /api/tasks?page=1&size=3 → 200，返回 3 条

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-016 — test_task_detail_found

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 工单详情-存在

**测试点：** 根据 ID 获取工单详情

**前置条件：** 系统中有该工单

**测试步骤：**
1. 发送 GET /api/tasks/{id} → 200

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-017 — test_task_detail_not_found

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 工单详情-不存在

**测试点：** 使用不存在 ID 获取详情验证 404

**前置条件：** 系统中不存在 ID=99999

**测试步骤：**
1. 发送 GET /api/tasks/99999 → 404

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-018 — test_update_task

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 更新工单

**测试点：** 更新工单部分字段

**前置条件：** 已登录；系统中有待更新工单

**测试步骤：**
1. 填写更新字段
2. 发送 PUT /api/tasks/{id} → 200

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-019 — test_valid_transition

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 工单状态流转-合法

**测试点：** 按合法状态流转

**前置条件：** 工单当前状态 pending

**测试步骤：**
1. PATCH status=in_progress → 200，状态变更

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-020 — test_invalid_transition

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 工单状态流转-非法

**测试点：** 跳过合法状态直接流转验证 400

**前置条件：** 工单当前状态 pending

**测试步骤：**
1. PATCH status=resolved（跳过 in_progress）→ 400

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-021 — test_assign_task

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 分配工单

**测试点：** 将工单分配给工程师

**前置条件：** 已登录；存在待分配工单和工程师

**测试步骤：**
1. 填写 assignee_id
2. PATCH /api/tasks/{id}/assign → 200

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-022 — test_delete_task

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 删除工单

**测试点：** 删除指定工单

**前置条件：** 已登录；存在可删除工单

**测试步骤：**
1. DELETE /api/tasks/{id} → 204

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-023 — test_filter_tasks

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 过滤工单

**测试点：** 按条件过滤工单列表

**前置条件：** 系统中有不同状态的工单

**测试步骤：**
1. POST /api/tasks/filter → 200

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-024 — test_task_stats

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 工单统计

**测试点：** 查看工单统计概览

**前置条件：** 系统中有多个不同状态的工单

**测试步骤：**
1. GET /api/tasks/stats/overview → 200

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-025 — test_create_comment

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 创建评论

**测试点：** 在工单下创建评论

**前置条件：** 已登录；系统中存在工单

**测试步骤：**
1. POST /api/tasks/{id}/comments → 201

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-026 — test_list_comments

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 查看评论列表

**测试点：** 查看工单下的评论列表

**前置条件：** 系统中存在带评论的工单

**测试步骤：**
1. GET /api/tasks/{id}/comments → 200

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-027 — test_comment_on_nonexistent_task

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 不存在工单下评论

**测试点：** 对不存在的工单评论验证 404

**前置条件：** ID=99999 不存在

**测试步骤：**
1. POST /api/tasks/99999/comments → 404

**结果：** PASS

---

## Phase 1 - Tasks

### API-TC-028 — test_ai_assign

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 AI 派单

**测试点：** 调用 AI 自动派单

**前置条件：** 已登录；有待分配工单

**测试步骤：**
1. POST /api/tasks/{id}/ai-assign → 200

**结果：** PASS

---

## Phase 2 - WeChat

### API-TC-029 — test_wechat_health

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 微信健康检查

**测试点：** 验证微信模块健康状态

**前置条件：** 微信服务已配置

**测试步骤：**
1. GET /api/wechat/health → 200

**结果：** PASS

---

## Phase 2 - WeChat

### API-TC-030 — test_get_menu

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 获取微信菜单

**测试点：** 获取微信自定义菜单配置

**前置条件：** 微信菜单已配置

**测试步骤：**
1. GET /api/wechat/get_menu → 200

**结果：** PASS

---

## Phase 2 - WeChat

### API-TC-031 — test_create_menu

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 创建微信菜单

**测试点：** 创建微信自定义菜单

**前置条件：** 已登录管理员

**测试步骤：**
1. POST /api/wechat/create_menu → 200

**结果：** PASS

---

## Phase 2 - WeChat

### API-TC-032 — test_send_message

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 发送微信消息

**测试点：** 通过微信发送消息给用户

**前置条件：** 目标用户 openid 有效

**测试步骤：**
1. POST /api/wechat/send_message → 200

**结果：** PASS

---

## Phase 2 - WeChat

### API-TC-033 — test_list_tags

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 微信标签列表

**测试点：** 获取用户标签列表

**前置条件：** 微信标签已配置

**测试步骤：**
1. GET /api/wechat → 200

**结果：** PASS

---

## Phase 2 - WeChat

### API-TC-034 — test_create_tag

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 创建微信标签

**测试点：** 创建用户标签

**前置条件：** 已登录管理员

**测试步骤：**
1. POST /api/wechat → 200

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-035 — test_create_conversation

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 创建会话

**测试点：** 创建新会话

**前置条件：** 已登录

**测试步骤：**
1. POST /api/conversations → 200

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-036 — test_list_conversations

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 会话列表

**测试点：** 获取当前用户会话列表

**前置条件：** 系统中有至少 1 条会话

**测试步骤：**
1. GET /api/conversations → 200

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-037 — test_get_conversation_detail

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 会话详情

**测试点：** 获取指定会话详情

**前置条件：** 系统中存在该会话

**测试步骤：**
1. GET /api/conversations/{id} → 200

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-038 — test_get_conversation_not_found

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 会话详情-不存在

**测试点：** 不存在会话验证 404

**前置条件：** ID=99999 不存在

**测试步骤：**
1. GET /api/conversations/99999 → 404

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-039 — test_qa_ask

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 QA 问答-同步

**测试点：** 发送同步 QA 问题并获取回答

**前置条件：** 已登录

**测试步骤：**
1. POST /api/qa/ask → 200

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-040 — test_qa_ask_stream

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 QA 问答-流式

**测试点：** 发送流式 QA 验证 SSE 格式

**前置条件：** 已登录

**测试步骤：**
1. POST /api/qa/ask/stream → SSE 流式响应

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-041 — test_create_message

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 创建消息

**测试点：** 在会话中创建消息

**前置条件：** 已登录；会话存在

**测试步骤：**
1. POST /api/messages → 201

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-042 — test_list_messages

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 消息列表

**测试点：** 获取会话消息列表

**前置条件：** 会话中包含多条消息

**测试步骤：**
1. GET /api/messages → 200

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-043 — test_get_my_tasks

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 我的工单

**测试点：** 获取分配给当前用户的工单

**前置条件：** 已登录；有分配给当前用户的工单

**测试步骤：**
1. GET /api/my-tasks/ → 200

**结果：** PASS

---

## Phase 3 - Call + QA + Stream

### API-TC-044 — test_create_my_task

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 创建我的工单

**测试点：** 创建分配给自己的工单

**前置条件：** 已登录

**测试步骤：**
1. POST /api/my-tasks/ → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-045 — test_list_tickets

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 工单管理列表

**测试点：** 管理员查看所有工单

**前置条件：** 已登录管理员

**测试步骤：**
1. GET /api/admin/tickets → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-046 — test_ticket_stats

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 工单管理统计

**测试点：** 管理员查看工单统计

**前置条件：** 已登录管理员；有工单数据

**测试步骤：**
1. GET /api/admin/tickets/stats → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-047 — test_list_projects

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 项目列表

**测试点：** 管理员查看项目列表

**前置条件：** 已登录管理员

**测试步骤：**
1. GET /api/admin/projects → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-048 — test_create_project

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 创建项目

**测试点：** 管理员创建新项目

**前置条件：** 已登录管理员

**测试步骤：**
1. POST /api/admin/projects → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-049 — test_list_risks

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 风险列表

**测试点：** 管理员查看项目风险列表

**前置条件：** 已登录管理员

**测试步骤：**
1. GET /api/admin/projects/risks → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-050 — test_dashboard_summary

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 仪表盘汇总

**测试点：** 管理员查看仪表盘汇总

**前置条件：** 已登录管理员

**测试步骤：**
1. GET /api/admin/dashboard/tickets/summary → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-051 — test_list_users

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 用户列表

**测试点：** 管理员查看用户列表

**前置条件：** 已登录管理员

**测试步骤：**
1. GET /api/admin/users/ → 200

**结果：** PASS

---

## Phase 4 - Admin

### API-TC-052 — test_list_roles

**属性：** 优先级 P1 · 自动化 · 冒烟 否 · 功能点 角色列表

**测试点：** 管理员查看角色列表

**前置条件：** 已登录管理员

**测试步骤：**
1. GET /api/admin/roles/ → 200

**结果：** PASS

---

## Phase 5 - Integrations

### API-TC-053 — test_list_sources

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 集成源列表

**测试点：** 查看集成数据源列表

**前置条件：** 已登录

**测试步骤：**
1. GET /api/integrations → 200

**结果：** PASS

---

## Phase 5 - Integrations

### API-TC-054 — test_create_mapping

**属性：** 优先级 P2 · 自动化 · 冒烟 否 · 功能点 创建任务用户映射

**测试点：** 创建任务用户映射

**前置条件：** 已登录

**测试步骤：**
1. POST /api/integrations/task-user-mappings → 200

**结果：** PASS

---

## Phase 4.5 — Admin Extensions（6 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 50 | test_create_daily_report | POST /api/admin/daily-reports | P1 |
| 51 | test_create_weekly_report | POST /api/admin/daily-reports | P1 |
| 52 | test_export_data | POST /api/admin/export | P2 |
| 53 | test_list_resources | GET /api/admin/resources | P1 |
| 54 | test_create_resource | POST /api/admin/resources | P1 |
| 55 | test_get_resource_detail | GET /api/admin/resources/{id} | P1 |
| 56 | test_update_resource | PUT /api/admin/resources/{id} | P1 |

## Phase 6 — AI Endpoints（3 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 57 | test_diagnose_task | POST /api/ai/task/diagnose | P1 |
| 58 | test_discuss_task | POST /api/ai/task/discuss | P1 |
| 59 | test_summarize_task | POST /api/ai/task/summarize | P1 |
