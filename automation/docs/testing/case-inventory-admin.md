# admin 模块 - 用例清单（代码驱动）

> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 120 条。

| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |
|------|--------|------|------|----------|------|
| test_daily_report | 后台管理 | 日报 | 正常：生成日报 | 正常流程 | `-` |
| test_daily_report_again | 后台管理 | 日报 | 正常：生成日报（重复） | 正常流程 | `-` |
| test_daily_report_by_date | 后台管理 | 日报 | 正常：按日期查询日报 | 正常流程 | `-` |
| test_daily_report_delete | 后台管理 | 日报 | 正常：删除日报 | 正常流程 | `-` |
| test_daily_report_detail | 后台管理 | 日报 | 正常：日报详情 | 正常流程 | `-` |
| test_daily_report_not_found | 后台管理 | 日报 | 异常：日报不存在 | 异常流程 | `-` |
| test_daily_report_search | 后台管理 | 日报 | 正常：日报搜索 | 正常流程 | `-` |
| test_daily_report_update | 后台管理 | 日报 | 正常：更新日报 | 正常流程 | `-` |
| test_daily_reports_list | 后台管理 | 日报 | 正常：日报列表 | 正常流程 | `-` |
| test_daily_reports_unauthorized | 后台管理 | 认证 | 权限：无 token 访问日报 401 | 权限 | `-` |
| test_dashboard_summary | 后台管理 | 看板 | 正常：看板汇总 | 正常流程 | `-` |
| test_export | 后台管理 | 导出 | 正常：导出数据 | 正常流程 | `-` |
| test_export_report | 后台管理 | 导出 | 正常：导出报表 | 正常流程 | `-` |
| test_folders_create_missing_name | 后台管理 | 文件夹 | 数据校验：创建文件夹缺名称 | 数据校验 | `-` |
| test_folders_create_ok | 后台管理 | 文件夹 | 正常：创建文件夹 | 正常流程 | `-` |
| test_folders_delete | 后台管理 | 文件夹 | 正常：删除文件夹 | 正常流程 | `-` |
| test_folders_detail | 后台管理 | 文件夹 | 正常：文件夹详情 | 正常流程 | `-` |
| test_folders_list | 后台管理 | 文件夹 | 正常：文件夹列表 | 正常流程 | `-` |
| test_folders_root | 后台管理 | 文件夹 | 正常：根文件夹 | 正常流程 | `-` |
| test_folders_root_children | 后台管理 | 文件夹 | 正常：根文件夹子级 | 正常流程 | `-` |
| test_folders_update | 后台管理 | 文件夹 | 正常：更新文件夹 | 正常流程 | `-` |
| test_permissions_create_duplicate | 后台管理 | 权限 | 异常：权限 code 重复 | 异常流程 | `-` |
| test_permissions_create_missing_code | 后台管理 | 权限 | 数据校验：缺 code 创建权限 | 数据校验 | `-` |
| test_permissions_create_ok | 后台管理 | 权限 | 正常：创建权限 | 正常流程 | `-` |
| test_permissions_delete | 后台管理 | 权限 | 正常：删除权限 | 正常流程 | `-` |
| test_permissions_detail | 后台管理 | 权限 | 正常：权限详情 | 正常流程 | `-` |
| test_permissions_detail_not_found | 后台管理 | 权限 | 异常：权限不存在 | 异常流程 | `-` |
| test_permissions_forbidden | 后台管理 | 权限 | 权限：非 admin 访问权限 403 | 权限 | `-` |
| test_permissions_list | 后台管理 | 权限 | 正常：权限列表 | 正常流程 | `-` |
| test_permissions_unauthorized | 后台管理 | 认证 | 权限：无 token 访问权限 401 | 权限 | `-` |
| test_permissions_update | 后台管理 | 权限 | 正常：更新权限 | 正常流程 | `-` |
| test_presigned_missing_params | 后台管理 | MinIO | 数据校验：预签名 URL 缺参数 | 数据校验 | `-` |
| test_presigned_ok | 后台管理 | MinIO | 正常：生成预签名 URL | 正常流程 | `-` |
| test_projects_create | 后台管理 | 项目 | 正常：创建项目 | 正常流程 | `-` |
| test_projects_delete | 后台管理 | 项目 | 正常：删除项目 | 正常流程 | `-` |
| test_projects_delete_not_found | 后台管理 | 项目 | 异常：删除项目不存在 | 异常流程 | `-` |
| test_projects_detail_not_found | 后台管理 | 项目 | 异常：项目详情不存在 | 异常流程 | `-` |
| test_projects_license_invalid_type | 后台管理 | 项目扩展 | 数据校验：许可 type 非法 | 数据校验 | `-` |
| test_projects_license_missing_fields | 后台管理 | 项目扩展 | 数据校验：申请许可缺字段 | 数据校验 | `-` |
| test_projects_license_ok | 后台管理 | 项目扩展 | 正常：申请项目许可 | 正常流程 | `-` |
| test_projects_license_query | 后台管理 | 项目扩展 | 正常：项目许可查询 | 正常流程 | `-` |
| test_projects_list | 后台管理 | 项目 | 正常：项目列表 | 正常流程 | `-` |
| test_projects_me | 后台管理 | 项目扩展 | 正常：我的项目 | 正常流程 | `-` |
| test_projects_update | 后台管理 | 项目 | 正常：更新项目 | 正常流程 | `-` |
| test_projects_update_not_found | 后台管理 | 项目 | 异常：项目不存在 | 异常流程 | `-` |
| test_resources_category | 后台管理 | 资源查询 | 正常：按分类查询 | 正常流程 | `-` |
| test_resources_create | 后台管理 | 资源 | 正常：创建资源 | 正常流程 | `-` |
| test_resources_delete | 后台管理 | 资源 | 正常：删除资源 | 正常流程 | `-` |
| test_resources_detail | 后台管理 | 资源 | 正常：资源详情 | 正常流程 | `-` |
| test_resources_detail | 后台管理 | 资源 | 正常：资源详情 | 正常流程 | `-` |
| test_resources_download_count | 后台管理 | 资源访问 | 正常：下载计数 | 正常流程 | `-` |
| test_resources_download_unavailable | 后台管理 | 资源访问 | 权限：下载不可用资源 403 | 权限 | `-` |
| test_resources_download_url_not_found | 后台管理 | 资源访问 | 异常：下载 URL 不可用 | 异常流程 | `-` |
| test_resources_hash_not_found | 后台管理 | 资源查询 | 异常：按哈希查询不存在 | 异常流程 | `-` |
| test_resources_like | 后台管理 | 资源访问 | 正常：资源点赞 | 正常流程 | `-` |
| test_resources_list | 后台管理 | 资源 | 正常：资源列表 | 正常流程 | `-` |
| test_resources_list | 后台管理 | 资源 | 正常：资源列表 | 正常流程 | `-` |
| test_resources_owner | 后台管理 | 资源查询 | 正常：按所有者查询 | 正常流程 | `-` |
| test_resources_recent | 后台管理 | 资源查询 | 正常：最近资源 | 正常流程 | `-` |
| test_resources_search | 后台管理 | 资源查询 | 正常：资源搜索 | 正常流程 | `-` |
| test_resources_stats_daily | 后台管理 | 资源查询 | 正常：资源日统计 | 正常流程 | `-` |
| test_resources_stats_summary | 后台管理 | 资源查询 | 正常：资源统计汇总 | 正常流程 | `-` |
| test_resources_sync_build | 后台管理 | 资源运维 | 正常：同步构建部署 | 正常流程 | `-` |
| test_resources_sync_oss | 后台管理 | 资源运维 | 正常：同步 OSS | 正常流程 | `-` |
| test_resources_thumbnail_not_found | 后台管理 | 资源访问 | 异常：缩略图 URL 不可用 | 异常流程 | `-` |
| test_resources_type_invalid | 后台管理 | 资源查询 | 数据校验：按类型查询非法类型 | 数据校验 | `-` |
| test_resources_type_ok | 后台管理 | 资源查询 | 正常：按类型查询 | 正常流程 | `-` |
| test_resources_update | 后台管理 | 资源 | 正常：更新资源 | 正常流程 | `-` |
| test_risks_create | 后台管理 | 风险 | 正常：创建风险 | 正常流程 | `-` |
| test_risks_create_missing_name | 后台管理 | 风险 | 数据校验：缺 name | 数据校验 | `-` |
| test_risks_delete | 后台管理 | 风险 | 正常：删除风险 | 正常流程 | `-` |
| test_risks_delete_not_found | 后台管理 | 风险 | 异常：删除风险不存在 | 异常流程 | `-` |
| test_risks_list | 后台管理 | 风险 | 正常：风险列表 | 正常流程 | `-` |
| test_risks_update | 后台管理 | 风险 | 正常：更新风险 | 正常流程 | `-` |
| test_risks_update_not_found | 后台管理 | 风险 | 异常：风险不存在 | 异常流程 | `-` |
| test_role_all_permissions | 后台管理 | 角色权限 | 正常：角色全部权限 | 正常流程 | `-` |
| test_role_auto_classify | 后台管理 | 角色权限 | 正常：角色自动分类 | 正常流程 | `-` |
| test_role_permissions_duplicate | 后台管理 | 角色权限 | 异常：重复授予权限 | 异常流程 | `-` |
| test_role_permissions_empty | 后台管理 | 角色权限 | 数据校验：授予权限空列表 | 数据校验 | `-` |
| test_role_permissions_grant | 后台管理 | 角色权限 | 正常：授予权限 | 正常流程 | `-` |
| test_role_permissions_list | 后台管理 | 角色权限 | 正常：角色权限列表 | 正常流程 | `-` |
| test_role_permissions_not_found | 后台管理 | 角色权限 | 异常：角色权限 404 | 异常流程 | `-` |
| test_roles_create | 后台管理 | 角色 | 正常：创建角色 | 正常流程 | `-` |
| test_roles_create_duplicate | 后台管理 | 角色 | 异常：角色已存在 | 异常流程 | `-` |
| test_roles_create_missing_name | 后台管理 | 角色 | 数据校验：缺 name | 数据校验 | `-` |
| test_roles_delete | 后台管理 | 角色 | 正常：删除角色 | 正常流程 | `-` |
| test_roles_forbidden | 后台管理 | 认证 | 权限：非 admin 访问角色 403 | 权限 | `-` |
| test_roles_list | 后台管理 | 角色 | 正常：角色列表 | 正常流程 | `-` |
| test_roles_list_cache | 后台管理 | 角色 | Redis：角色缓存失效 | Redis | `-` |
| test_roles_update | 后台管理 | 角色 | 正常：更新角色 | 正常流程 | `-` |
| test_roles_update_not_found | 后台管理 | 角色 | 异常：角色不存在 | 异常流程 | `-` |
| test_tickets_list | 后台管理 | 工单总览 | 正常：工单列表 | 正常流程 | `-` |
| test_tickets_stats | 后台管理 | 工单总览 | 正常：工单统计 | 正常流程 | `-` |
| test_tickets_stats_again | 后台管理 | 工单总览 | 正常：工单统计（重复） | 正常流程 | `-` |
| test_user_detail | 后台管理 | 用户扩展 | 正常：用户详情 | 正常流程 | `-` |
| test_user_detail_not_found | 后台管理 | 用户扩展 | 异常：用户详情不存在 | 异常流程 | `-` |
| test_user_options | 后台管理 | 用户扩展 | 正常：用户选项 | 正常流程 | `-` |
| test_user_reporters_missing_project | 后台管理 | 用户扩展 | 数据校验：上报人缺 project_id | 数据校验 | `-` |
| test_user_reporters_ok | 后台管理 | 用户扩展 | 正常：上报人列表 | 正常流程 | `-` |
| test_user_roles_assign | 后台管理 | 用户扩展 | 正常：分配角色 | 正常流程 | `-` |
| test_user_roles_empty_roles | 后台管理 | 用户扩展 | 数据校验：分配角色空 role_ids | 数据校验 | `-` |
| test_user_roles_missing_project | 后台管理 | 用户扩展 | 数据校验：分配角色缺 project_id | 数据校验 | `-` |
| test_user_roles_remove | 后台管理 | 用户扩展 | 正常：移除角色 | 正常流程 | `-` |
| test_user_roles_user_not_found | 后台管理 | 用户扩展 | 异常：分配角色用户不存在 | 异常流程 | `-` |
| test_user_uspinfo_missing_name | 后台管理 | 用户扩展 | 数据校验：更新 USP 信息缺 name | 数据校验 | `-` |
| test_user_uspinfo_ok | 后台管理 | 用户扩展 | 正常：更新 USP 信息 | 正常流程 | `-` |
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
| test_usp_username_missing_name | 后台管理 | 用户扩展 | 数据校验：USP 用户名缺 name | 数据校验 | `-` |
| test_usp_username_ok | 后台管理 | 用户扩展 | 正常：USP 用户名查询 | 正常流程 | `-` |
| test_weekly_report | 后台管理 | 日报 | 正常：生成周报 | 正常流程 | `-` |
