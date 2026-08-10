# tasks 模块 - 用例清单（代码驱动）

> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 32 条。

| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |
|------|--------|------|------|----------|------|
| test_ai_analyze_cache | 系统任务 | AI 分析 | Redis：AI 分析缓存 | Redis | `POST /api/ai/task/analyze` |
| test_ai_assign | 系统任务 | AI 指派 | AI：AI 自动指派 | AI | `POST /api/tasks/1/ai-assign` |
| test_assign_admin_transfer | 系统任务 | 指派工单 | 正常：admin 转单 | 正常流程 | `PATCH /api/tasks/1/assign` |
| test_assign_clear | 系统任务 | 指派工单 | 正常：清除指派 | 正常流程 | `PATCH /api/tasks/1/assign` |
| test_assign_customer_role | 系统任务 | 指派工单 | 正常：customer 接单 | 正常流程 | `PATCH /api/tasks/1/assign` |
| test_assign_engineer | 系统任务 | 指派工单 | 正常：指派工程师 | 正常流程 | `PATCH /api/tasks/1/assign` |
| test_assign_engineer_no_conflict | 系统任务 | 指派工单 | 正常：接单（mock 暂不实现冲突检测） | 正常流程 | `PATCH /api/tasks/1/assign` |
| test_assign_engineer_role | 系统任务 | 指派工单 | 正常：engineer 接单 | 正常流程 | `PATCH /api/tasks/1/assign` |
| test_assign_status_in_progress | 系统任务 | 指派工单 | 正常：工程师接单 | 正常流程 | `PATCH /api/tasks/1/assign` |
| test_comment_create | 系统任务 | 评论 | 正常：添加评论 | 正常流程 | `POST /api/tasks/1/comments` |
| test_comment_task_not_found | 系统任务 | 评论 | 异常：评论不存在的任务 | 异常流程 | `POST /api/tasks/99999/comments` |
| test_create_task_basic | 系统任务 | 创建工单 | 正常：基础字段创建 | 正常流程 | `POST /api/tasks` |
| test_create_task_empty_title | 系统任务 | 创建工单 | 数据校验：title 为空 | 数据校验 | `POST /api/tasks` |
| test_create_task_full_fields | 系统任务 | 创建工单 | 正常：全字段创建 | 正常流程 | `POST /api/tasks` |
| test_create_task_missing_title | 系统任务 | 创建工单 | 数据校验：缺 title | 数据校验 | `POST /api/tasks` |
| test_create_task_type_bug | 系统任务 | 创建工单 | 正常：type=bug 创建 | 正常流程 | `POST /api/tasks` |
| test_create_task_type_invalid | 系统任务 | 创建工单 | 数据校验：type 非法 | 数据校验 | `POST /api/tasks` |
| test_create_task_type_requirement | 系统任务 | 创建工单 | 正常：type=requirement 创建 | 正常流程 | `POST /api/tasks` |
| test_create_task_type_support | 系统任务 | 创建工单 | 正常：type=support 创建 | 正常流程 | `POST /api/tasks` |
| test_delete_task | 系统任务 | 删除工单 | 正常：删除任务 | 正常流程 | `DELETE /api/tasks/1` |
| test_filter_keyword | 系统任务 | 筛选 | 正常：关键词筛选 | 正常流程 | `POST /api/tasks/filter` |
| test_full_flow_create_to_closed | 系统任务 | 全链路 | 全链路：建单→处理中→已解决→已关闭 | 正常流程 | `POST /api/tasks` |
| test_get_task_detail | 系统任务 | 查询工单 | 正常：任务详情 | 正常流程 | `GET /api/tasks/1` |
| test_get_task_not_found | 系统任务 | 查询工单 | 异常：任务不存在 | 异常流程 | `GET /api/tasks/99999` |
| test_list_tasks | 系统任务 | 查询工单 | 正常：任务列表 | 正常流程 | `GET /api/tasks` |
| test_list_tasks_pagination | 系统任务 | 查询工单 | 正常：分页列表 | 正常流程 | `GET /api/tasks` |
| test_list_tasks_size_200 | 系统任务 | 查询工单 | 正常：分页 size=200 | 正常流程 | `GET /api/tasks?size=200` |
| test_stats_overview | 系统任务 | 统计 | 正常：状态统计 | 正常流程 | `GET /api/tasks/stats/overview` |
| test_status_transition_closed_invalid | 系统任务 | 状态流转 | 状态流转：非法流转 closed | 状态流转 | `PATCH /api/tasks/1/status` |
| test_status_transition_closed_reopen | 系统任务 | 状态流转 | 状态流转：closed 不可重开 | 状态流转 | `PATCH /api/tasks/1/status` |
| test_status_transition_valid | 系统任务 | 状态流转 | 状态流转：合法流转 | 状态流转 | `PATCH /api/tasks/1/status` |
| test_update_task | 系统任务 | 更新工单 | 正常：更新任务 | 正常流程 | `PUT /api/tasks/1` |
