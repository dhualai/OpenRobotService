# integrations 模块 - 用例清单（代码驱动）

> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 16 条。

| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |
|------|--------|------|------|----------|------|
| test_mappings_create | 外部集成 | 映射 | 正常：创建映射 | 正常流程 | `-` |
| test_mappings_create_duplicate | 外部集成 | 映射 | 异常：重复映射 409 | 异常流程 | `-` |
| test_mappings_create_missing_source | 外部集成 | 映射 | 数据校验：缺 source | 数据校验 | `-` |
| test_mappings_delete | 外部集成 | 映射 | 正常：删除映射 | 正常流程 | `-` |
| test_mappings_delete_not_found | 外部集成 | 映射 | 异常：删除映射不存在 | 异常流程 | `-` |
| test_mappings_list | 外部集成 | 映射 | 正常：映射列表 | 正常流程 | `-` |
| test_mappings_unauthorized | 外部集成 | 映射 | 权限：未认证访问映射 | 权限 | `-` |
| test_mappings_update | 外部集成 | 映射 | 正常：更新映射 | 正常流程 | `-` |
| test_mappings_update_not_found | 外部集成 | 映射 | 异常：更新映射不存在 | 异常流程 | `-` |
| test_sources_list | 外部集成 | 任务源 | 正常：任务源列表 | 正常流程 | `-` |
| test_sources_missing_key | 外部集成 | 任务源 | 权限：缺 API Key 401 | 权限 | `-` |
| test_sources_sync_not_registered | 外部集成 | 任务源 | 异常：同步未注册源 404 | 异常流程 | `-` |
| test_sources_sync_ok | 外部集成 | 任务源 | 正常：同步已注册源 | 正常流程 | `-` |
| test_sources_wrong_key | 外部集成 | 任务源 | 权限：错误 API Key 401 | 权限 | `-` |
| test_wecom_sync_ok | 外部集成 | 企微同步 | 正常：同步企微项目 | 正常流程 | `-` |
| test_wecom_sync_unauthorized | 外部集成 | 企微同步 | 权限：未认证 401 | 权限 | `-` |
