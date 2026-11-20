# admin 模块 - 用例清单（代码驱动）

> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 45 条。

| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |
|------|--------|------|------|----------|------|
| test_daily_report | 后台管理 | 日报 | 正常：生成日报 | 正常流程 | `-` |
| test_daily_report_again | 后台管理 | 日报 | 正常：生成日报（重复） | 正常流程 | `-` |
| test_dashboard_summary | 后台管理 | 看板 | 正常：看板汇总 | 正常流程 | `-` |
| test_export | 后台管理 | 导出 | 正常：导出数据 | 正常流程 | `-` |
| test_export_report | 后台管理 | 导出 | 正常：导出报表 | 正常流程 | `-` |
| test_projects_create | 后台管理 | 项目 | 正常：创建项目 | 正常流程 | `-` |
| test_projects_delete | 后台管理 | 项目 | 正常：删除项目 | 正常流程 | `-` |
| test_projects_delete_not_found | 后台管理 | 项目 | 异常：删除项目不存在 | 异常流程 | `-` |
| test_projects_detail_not_found | 后台管理 | 项目 | 异常：项目详情不存在 | 异常流程 | `-` |
| test_projects_list | 后台管理 | 项目 | 正常：项目列表 | 正常流程 | `-` |
| test_projects_update | 后台管理 | 项目 | 正常：更新项目 | 正常流程 | `-` |
| test_projects_update_not_found | 后台管理 | 项目 | 异常：项目不存在 | 异常流程 | `-` |
| test_resources_create | 后台管理 | 资源 | 正常：创建资源 | 正常流程 | `-` |
| test_resources_detail | 后台管理 | 资源 | 正常：资源详情 | 正常流程 | `-` |
| test_resources_list | 后台管理 | 资源 | 正常：资源列表 | 正常流程 | `-` |
| test_risks_create | 后台管理 | 风险 | 正常：创建风险 | 正常流程 | `-` |
| test_risks_create_missing_name | 后台管理 | 风险 | 数据校验：缺 name | 数据校验 | `-` |
| test_risks_delete | 后台管理 | 风险 | 正常：删除风险 | 正常流程 | `-` |
| test_risks_delete_not_found | 后台管理 | 风险 | 异常：删除风险不存在 | 异常流程 | `-` |
| test_risks_list | 后台管理 | 风险 | 正常：风险列表 | 正常流程 | `-` |
| test_risks_update | 后台管理 | 风险 | 正常：更新风险 | 正常流程 | `-` |
| test_risks_update_not_found | 后台管理 | 风险 | 异常：风险不存在 | 异常流程 | `-` |
| test_roles_create | 后台管理 | 角色 | 正常：创建角色 | 正常流程 | `-` |
| test_roles_create_duplicate | 后台管理 | 角色 | 异常：角色已存在 | 异常流程 | `-` |
| test_roles_create_missing_name | 后台管理 | 角色 | 数据校验：缺 name | 数据校验 | `-` |
| test_roles_delete | 后台管理 | 角色 | 正常：删除角色 | 正常流程 | `-` |
| test_roles_list | 后台管理 | 角色 | 正常：角色列表 | 正常流程 | `-` |
| test_roles_list_cache | 后台管理 | 角色 | Redis：角色缓存失效 | Redis | `-` |
| test_roles_update | 后台管理 | 角色 | 正常：更新角色 | 正常流程 | `-` |
| test_roles_update_not_found | 后台管理 | 角色 | 异常：角色不存在 | 异常流程 | `-` |
| test_tickets_list | 后台管理 | 工单总览 | 正常：工单列表 | 正常流程 | `-` |
| test_tickets_stats | 后台管理 | 工单总览 | 正常：工单统计 | 正常流程 | `-` |
| test_tickets_stats_again | 后台管理 | 工单总览 | 正常：工单统计（重复） | 正常流程 | `-` |
| test_users_create | 后台管理 | 用户 | 正常：创建用户 | 正常流程 | `-` |
| test_users_create_duplicate | 后台管理 | 用户 | 异常：用户名已存在 | 异常流程 | `-` |
| test_users_create_missing_username | 后台管理 | 用户 | 数据校验：缺 username | 数据校验 | `-` |
| test_users_create_unauthorized | 后台管理 | 用户 | 权限：未认证创建用户 | 权限 | `-` |
| test_users_delete_cascade | 后台管理 | 用户 | 数据库：删除用户级联事务 | 数据库 | `-` |
| test_users_forbidden | 后台管理 | 用户 | 权限：非 admin 访问 403 | 权限 | `-` |
| test_users_list | 后台管理 | 用户 | 正常：用户列表 | 正常流程 | `-` |
| test_users_list_cache | 后台管理 | 用户 | Redis：用户列表缓存 | Redis | `-` |
| test_users_unauthorized | 后台管理 | 用户 | 权限：无 token 访问 401 | 权限 | `-` |
| test_users_update | 后台管理 | 用户 | 正常：更新用户 | 正常流程 | `-` |
| test_users_update_not_found | 后台管理 | 用户 | 异常：用户不存在 | 异常流程 | `-` |
| test_weekly_report | 后台管理 | 日报 | 正常：生成周报 | 正常流程 | `-` |
