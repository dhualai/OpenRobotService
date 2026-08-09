# wechat 模块 - 用例清单（代码驱动）

> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 27 条。

| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |
|------|--------|------|------|----------|------|
| test_backend_notify_at_all | 微信 | 消息 | 异常：backend/notify @所有人 | 异常流程 | `-` |
| test_broadcast | 微信 | 消息 | 正常：群发消息 | 正常流程 | `-` |
| test_broadcast_missing_content | 微信 | 消息 | 数据校验：群发缺 content | 数据校验 | `-` |
| test_import_content_not_list | 微信 | 导入 | 数据校验：content 非列表 | 数据校验 | `-` |
| test_import_missing_project | 微信 | 导入 | 数据校验：缺 project | 数据校验 | `-` |
| test_import_ok | 微信 | 导入 | 正常：导入数据 | 正常流程 | `-` |
| test_login_missing_openid | 微信 | 登录 | 数据校验：缺 openid | 数据校验 | `-` |
| test_login_ok | 微信 | 登录 | 正常：openid 登录 | 正常流程 | `-` |
| test_menu_create | 微信 | 菜单 | 正常：创建菜单 | 正常流程 | `-` |
| test_menu_delete | 微信 | 菜单 | 正常：删除菜单 | 正常流程 | `-` |
| test_menu_get | 微信 | 菜单 | 正常：获取菜单 | 正常流程 | `-` |
| test_permissions_ok | 微信 | 权限 | 正常：获取用户权限 | 正常流程 | `-` |
| test_permissions_user_not_found | 微信 | 权限 | 异常：用户不存在 | 异常流程 | `-` |
| test_send_link_missing_url | 微信 | 消息 | 数据校验：链接消息缺 url | 数据校验 | `-` |
| test_send_message | 微信 | 消息 | 正常：发送模板消息 | 正常流程 | `-` |
| test_send_message_missing_openid | 微信 | 消息 | 数据校验：缺 open_id | 数据校验 | `-` |
| test_tag_batch_over_limit | 微信 | 标签 | 数据校验：批量打标超 100 人 | 数据校验 | `-` |
| test_tag_create | 微信 | 标签 | 正常：创建标签 | 正常流程 | `-` |
| test_tag_create_missing_name | 微信 | 标签 | 数据校验：缺 name | 数据校验 | `-` |
| test_tag_delete | 微信 | 标签 | 正常：删除标签 | 正常流程 | `-` |
| test_tag_delete_not_found | 微信 | 标签 | 异常：删除标签不存在 | 异常流程 | `-` |
| test_tag_fans | 微信 | 标签 | 正常：标签粉丝列表 | 正常流程 | `-` |
| test_tag_list | 微信 | 标签 | 正常：标签列表 | 正常流程 | `-` |
| test_tag_update | 微信 | 标签 | 正常：更新标签 | 正常流程 | `-` |
| test_tag_update_not_found | 微信 | 标签 | 异常：更新标签不存在 | 异常流程 | `-` |
| test_tag_user | 微信 | 标签 | 正常：用户标签查询 | 正常流程 | `-` |
| test_webnotify_at_all | 微信 | 消息 | 异常：webnotify @所有人 | 异常流程 | `-` |
