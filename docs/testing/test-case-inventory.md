# 测试用例全量清单

> 本文列出 automation 框架中所有已实现测试用例的完整清单。
> 按模块分组，每条用例包含测试函数、接口、覆盖点、优先级和状态。

---

## 总览

| 模块 | 文件数 | 用例数 | 状态 |
|------|--------|--------|------|
| API 测试（Phase 0-5）| 7 | 54 | ✅ 已完成 |
| DB 测试 | 3 | 26 | ✅ 已完成 |
| E2E 测试 | 1 | 6 | ✅ 已完成 |
| 基础设施测试（config/logger/clients/assertions/fixtures）| 9 | 86 | ✅ 已完成 |
| **合计** | **20** | **172** | |
| UI 测试 | — | — | ⬜ 待实现 |
| AI 测试 | — | — | ⬜ 待实现 |

---

## API 测试（54 用例）

### Phase 0 — Auth + Health（10 用例）

| # | 测试函数 | 接口 | 覆盖点 | 优先级 | 状态 |
|---|---------|------|--------|--------|------|
| 1 | test_health_check | GET /api/health | 健康检查返回 200 | P0 | ✅ |
| 2 | test_health_response_structure | GET /api/health | 响应结构校验 | P0 | ✅ |
| 3 | test_login_success | POST /auth/login | 正常登录返回 JWT | P0 | ✅ |
| 4 | test_login_wrong_password | POST /auth/login | 错误密码→401 | P0 | ✅ |
| 5 | test_login_user_not_found | POST /auth/login | 不存在用户→401 | P0 | ✅ |
| 6 | test_login_empty_username | POST /auth/login | 空字段→422 | P1 | ✅ |
| 7 | test_login_missing_fields | POST /auth/login | 缺字段→422 | P1 | ✅ |
| 8 | test_get_current_user_with_valid_token | GET /auth/me | 有效 Token 返回用户信息 | P0 | ✅ |
| 9 | test_get_current_user_no_token | GET /auth/me | 无 Token→401 | P1 | ✅ |
| 10 | test_get_current_user_forged_token | GET /auth/me | 伪造 Token→401 | P1 | ✅ |

### Phase 1 — Tasks CRUD + 状态流转（18 用例）

| # | 测试函数 | 接口 | 覆盖点 | 优先级 | 状态 |
|---|---------|------|--------|--------|------|
| 11 | test_create_task_minimal | POST /api/tasks | 必填字段创建 | P0 | ✅ |
| 12 | test_create_task_full_fields | POST /api/tasks | 全字段创建 | P0 | ✅ |
| 13 | test_create_task_missing_title | POST /api/tasks | 缺字段→422 | P1 | ✅ |
| 14 | test_task_list | GET /api/tasks | 工单列表 | P0 | ✅ |
| 15 | test_task_list_pagination | GET /api/tasks | 分页参数 | P1 | ✅ |
| 16 | test_task_detail_found | GET /api/tasks/{id} | 工单详情 | P0 | ✅ |
| 17 | test_task_detail_not_found | GET /api/tasks/{id} | 不存在→404 | P1 | ✅ |
| 18 | test_update_task | PUT /api/tasks/{id} | 更新工单 | P0 | ✅ |
| 19 | test_valid_transition | PATCH .../status | 合法流转 | P0 | ✅ |
| 20 | test_invalid_transition | PATCH .../status | 非法流转→400 | P1 | ✅ |
| 21 | test_assign_task | PATCH .../assign | 分配处理人 | P0 | ✅ |
| 22 | test_delete_task | DELETE /api/tasks/{id} | 删除工单 | P1 | ✅ |
| 23 | test_filter_tasks | POST /api/tasks/filter | 关键词筛选 | P1 | ✅ |
| 24 | test_task_stats | GET .../stats/overview | 工单统计 | P1 | ✅ |
| 25 | test_create_comment | POST .../comments | 创建评论 | P1 | ✅ |
| 26 | test_list_comments | GET .../comments | 评论列表 | P1 | ✅ |
| 27 | test_comment_on_nonexistent_task | POST .../99999/comments | 不存在工单→404 | P1 | ✅ |
| 28 | test_ai_assign | POST .../ai-assign | AI 自动分配 | P2 | ✅ |

### Phase 2 — WeChat（6 用例）

| # | 测试函数 | 接口 | 覆盖点 | 优先级 | 状态 |
|---|---------|------|--------|--------|------|
| 29 | test_wechat_health | GET /api/wechat/health | 健康检查 | P1 | ✅ |
| 30 | test_get_menu | GET /api/wechat/get_menu | 获取菜单 | P1 | ✅ |
| 31 | test_create_menu | POST /api/wechat/create_menu | 创建菜单 | P2 | ✅ |
| 32 | test_send_message | POST /api/wechat/send_message | 发送消息 | P2 | ✅ |
| 33 | test_list_tags | GET /api/wechat | 标签列表 | P2 | ✅ |
| 34 | test_create_tag | POST /api/wechat | 创建标签 | P2 | ✅ |

### Phase 3 — Call + QA + Stream（10 用例）

| # | 测试函数 | 接口 | 覆盖点 | 优先级 | 状态 |
|---|---------|------|--------|--------|------|
| 35 | test_create_conversation | POST /api/conversations | 创建会话 | P0 | ✅ |
| 36 | test_list_conversations | GET /api/conversations | 会话列表 | P0 | ✅ |
| 37 | test_get_conversation_detail | GET .../{id} | 会话详情 | P0 | ✅ |
| 38 | test_get_conversation_not_found | GET .../99999 | 不存在→404 | P1 | ✅ |
| 39 | test_qa_ask | POST /api/qa/ask | QA 问答 | P0 | ✅ |
| 40 | test_qa_ask_stream | POST /api/qa/ask/stream | SSE 流式 QA | P1 | ✅ |
| 41 | test_create_message | POST /api/messages | 创建消息 | P1 | ✅ |
| 42 | test_list_messages | GET /api/messages | 消息列表 | P1 | ✅ |
| 43 | test_get_my_tasks | GET /api/my-tasks/ | 我的工单 | P1 | ✅ |
| 44 | test_create_my_task | POST /api/my-tasks/ | 创建工单 | P1 | ✅ |

### Phase 4 — Admin（8 用例）

| # | 测试函数 | 接口 | 覆盖点 | 优先级 | 状态 |
|---|---------|------|--------|--------|------|
| 45 | test_list_tickets | GET /api/admin/tickets | 工单列表 | P1 | ✅ |
| 46 | test_ticket_stats | GET .../tickets/stats | 工单统计 | P1 | ✅ |
| 47 | test_list_projects | GET /api/admin/projects | 项目列表 | P1 | ✅ |
| 48 | test_create_project | POST /api/admin/projects | 创建项目 | P1 | ✅ |
| 49 | test_list_risks | GET .../projects/risks | 风险列表 | P2 | ✅ |
| 50 | test_dashboard_summary | GET .../dashboard/tickets/summary | 仪表盘 | P2 | ✅ |
| 51 | test_list_users | GET /api/admin/users/ | 用户列表 | P1 | ✅ |
| 52 | test_list_roles | GET /api/admin/roles/ | 角色列表 | P1 | ✅ |

### Phase 5 — Integrations（2 用例）

| # | 测试函数 | 接口 | 覆盖点 | 优先级 | 状态 |
|---|---------|------|--------|--------|------|
| 53 | test_list_sources | GET /api/integrations | 外部源列表 | P2 | ✅ |
| 54 | test_create_mapping | POST .../task-user-mappings | 创建映射 | P2 | ✅ |

---

## DB 测试（26 用例）

### MySQLChecker（11 用例）

| # | 测试函数 | 覆盖点 | 状态 |
|---|---------|--------|------|
| 55 | test_assert_row_exists_found | 行存在返回数据 | ✅ |
| 56 | test_assert_row_exists_not_found | 行不存在→断言失败 | ✅ |
| 57 | test_assert_row_not_exists | 行确实不存在（静默）| ✅ |
| 58 | test_assert_row_not_exists_fail | 行意外存在→断言失败 | ✅ |
| 59 | test_assert_row_count_exact_pass | 精确行数匹配 | ✅ |
| 60 | test_assert_row_count_exact_fail | 行数不匹配→断言失败 | ✅ |
| 61 | test_assert_row_count_min | 最小行数约束 | ✅ |
| 62 | test_assert_row_count_max | 最大行数约束 | ✅ |
| 63 | test_assert_matches | 字段子集匹配 | ✅ |
| 64 | test_assert_column_values | 列枚举值正确 | ✅ |
| 65 | test_assert_column_values_unexpected | 列值不在预期内→断言失败 | ✅ |

### RedisChecker（8 用例）

| # | 测试函数 | 覆盖点 | 状态 |
|---|---------|--------|------|
| 66 | test_assert_key_exists | 键存在返回值 | ✅ |
| 67 | test_assert_key_not_exists | 键不存在（静默）| ✅ |
| 68 | test_assert_key_not_exists_fail | 键意外存在→断言失败 | ✅ |
| 69 | test_assert_value_equals_pass | 值精确匹配 | ✅ |
| 70 | test_assert_value_equals_fail | 值不匹配→断言失败 | ✅ |
| 71 | test_assert_value_contains_pass | 值包含子串 | ✅ |
| 72 | test_assert_value_contains_fail | 值不包含子串→断言失败 | ✅ |
| 73 | test_assert_key_not_exists_on_missing | 不存在的键取值→断言失败 | ✅ |

### QdrantChecker（7 用例）

| # | 测试函数 | 覆盖点 | 状态 |
|---|---------|--------|------|
| 74 | test_assert_collection_exists | 集合存在 | ✅ |
| 75 | test_assert_collection_not_exists | 集合确实不存在 | ✅ |
| 76 | test_assert_collection_not_exists_fail | 集合意外存在→断言失败 | ✅ |
| 77 | test_assert_search_returns | 搜索结果含指定 ID | ✅ |
| 78 | test_assert_search_returns_missing | 缺少指定 ID→断言失败 | ✅ |
| 79 | test_assert_point_count | 精确点数匹配 | ✅ |
| 80 | test_assert_point_count_wrong | 点数不匹配→断言失败 | ✅ |

---

## E2E 测试（6 用例）

| # | 测试函数 | 流程 | 涉及模块 | 状态 |
|---|---------|------|---------|------|
| 81 | test_full_ticket_lifecycle | 创建→分派→处理→诊断→解决→关闭 | Auth + Tasks | ✅ |
| 82 | test_invalid_status_transition_blocked | 跳过状态非法流转→400 | Tasks | ✅ |
| 83 | test_qa_to_conversation_flow | QA 提问→创建会话 | QA + Conversation | ✅ |
| 84 | test_create_ticket_after_qa | QA 后创建工单 | QA + Tasks | ✅ |
| 85 | test_multi_role_collaboration | 管理员建单→客户查看→工程师处理 | Auth + Tasks + MyTasks | ✅ |
| 86 | test_ai_assign_after_ticket_creation | 建单后 AI 派单 | Tasks | ✅ |

---

## 基础设施测试（86 用例）

| 模块 | 文件 | 用例数 | 状态 |
|------|------|--------|------|
| Config | config/tests/ | 25 | ✅ |
| Logger | logger/tests/ | 16 | ✅ |
| Clients | clients/tests/ | 41 | ✅ |
| Assertions | assertions/tests/ | 23 | ✅ |
| Fixtures | fixtures/tests/ | 7 | ✅ |

基础设施测试详情见各模块 `tests/` 目录。
这些测试不依赖外部服务，验证框架本身行为。

---

## 待实现测试

| 模块 | 说明 | 前置条件 |
|------|------|----------|
| UI 测试 | Playwright + Page Object | 后端运行中 + 浏览器 |
| AI 测试 | LLM Evaluator + Scenario | Mock LLM 服务 |
