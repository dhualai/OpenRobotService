# API 测试用例清单

> 5 个 Phase，54 个测试用例。详情见 [api-test-plan.md](api-test-plan.md)。

## Phase 0 — Auth + Health（10 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 1 | test_health_check | GET /api/health | P0 |
| 2 | test_health_response_structure | GET /api/health | P0 |
| 3 | test_login_success | POST /auth/login | P0 |
| 4 | test_login_wrong_password | POST /auth/login | P0 |
| 5 | test_login_user_not_found | POST /auth/login | P0 |
| 6 | test_login_empty_username | POST /auth/login | P1 |
| 7 | test_login_missing_fields | POST /auth/login | P1 |
| 8 | test_get_current_user_with_valid_token | GET /auth/me | P0 |
| 9 | test_get_current_user_no_token | GET /auth/me | P1 |
| 10 | test_get_current_user_forged_token | GET /auth/me | P1 |

## Phase 1 — Tasks（18 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 11 | test_create_task_minimal | POST /api/tasks | P0 |
| 12 | test_create_task_full_fields | POST /api/tasks | P0 |
| 13 | test_create_task_missing_title | POST /api/tasks | P1 |
| 14 | test_task_list | GET /api/tasks | P0 |
| 15 | test_task_list_pagination | GET /api/tasks | P1 |
| 16 | test_task_detail_found | GET /api/tasks/{id} | P0 |
| 17 | test_task_detail_not_found | GET /api/tasks/{id} | P1 |
| 18 | test_update_task | PUT /api/tasks/{id} | P0 |
| 19 | test_valid_transition | PATCH .../status | P0 |
| 20 | test_invalid_transition | PATCH .../status | P1 |
| 21 | test_assign_task | PATCH .../assign | P0 |
| 22 | test_delete_task | DELETE /api/tasks/{id} | P1 |
| 23 | test_filter_tasks | POST /api/tasks/filter | P1 |
| 24 | test_task_stats | GET .../stats/overview | P1 |
| 25 | test_create_comment | POST .../comments | P1 |
| 26 | test_list_comments | GET .../comments | P1 |
| 27 | test_comment_on_nonexistent_task | POST .../99999/comments | P1 |
| 28 | test_ai_assign | POST .../ai-assign | P2 |

## Phase 2 — WeChat（6 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 29 | test_wechat_health | GET /api/wechat/health | P1 |
| 30 | test_get_menu | GET /api/wechat/get_menu | P1 |
| 31 | test_create_menu | POST /api/wechat/create_menu | P2 |
| 32 | test_send_message | POST /api/wechat/send_message | P2 |
| 33 | test_list_tags | GET /api/wechat | P2 |
| 34 | test_create_tag | POST /api/wechat | P2 |

## Phase 3 — Call + QA + Stream（10 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 35 | test_create_conversation | POST /api/conversations | P0 |
| 36 | test_list_conversations | GET /api/conversations | P0 |
| 37 | test_get_conversation_detail | GET .../{id} | P0 |
| 38 | test_get_conversation_not_found | GET .../99999 | P1 |
| 39 | test_qa_ask | POST /api/qa/ask | P0 |
| 40 | test_qa_ask_stream | POST /api/qa/ask/stream | P1 |
| 41 | test_create_message | POST /api/messages | P1 |
| 42 | test_list_messages | GET /api/messages | P1 |
| 43 | test_get_my_tasks | GET /api/my-tasks/ | P1 |
| 44 | test_create_my_task | POST /api/my-tasks/ | P1 |

## Phase 4 — Admin（8 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 45 | test_list_tickets | GET /api/admin/tickets | P1 |
| 46 | test_ticket_stats | GET .../tickets/stats | P1 |
| 47 | test_list_projects | GET /api/admin/projects | P1 |
| 48 | test_create_project | POST /api/admin/projects | P1 |
| 49 | test_list_risks | GET .../projects/risks | P2 |
| 50 | test_dashboard_summary | GET .../dashboard/... | P2 |
| 51 | test_list_users | GET /api/admin/users/ | P1 |
| 52 | test_list_roles | GET /api/admin/roles/ | P1 |

## Phase 5 — Integrations（2 用例）

| # | 测试函数 | 接口 | 优先级 |
|---|---------|------|--------|
| 53 | test_list_sources | GET /api/integrations | P2 |
| 54 | test_create_mapping | POST .../task-user-mappings | P2 |

**合计：54 用例 ✅**
