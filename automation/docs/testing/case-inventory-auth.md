# auth 模块 - 用例清单（代码驱动）

> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 12 条。

| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |
|------|--------|------|------|----------|------|
| test_login_admin | 认证 | 登录 | 正常：登录成功 | 正常流程 | `-` |
| test_login_customer | 认证 | 登录 | 正常：客户登录 | 正常流程 | `-` |
| test_login_empty_username | 认证 | 登录 | 数据校验：用户名为空 | 数据校验 | `-` |
| test_login_engineer | 认证 | 登录 | 正常：工程师登录 | 正常流程 | `-` |
| test_login_missing_fields | 认证 | 登录 | 数据校验：缺少 username/password | 数据校验 | `-` |
| test_login_user_not_found | 认证 | 登录 | 异常：用户不存在 | 异常流程 | `-` |
| test_login_wrong_password | 认证 | 登录 | 异常：密码错误 | 异常流程 | `-` |
| test_me_admin | 认证 | 当前用户 | 正常：获取当前用户(admin) | 正常流程 | `-` |
| test_me_unauthorized | 认证 | 当前用户 | 权限：无 token 访问 | 权限 | `-` |
| test_wechat_callback | 认证 | 微信 | 正常：微信回调 POST | 正常流程 | `-` |
| test_wechat_health | 认证 | 微信 | 正常：微信健康检查 | 正常流程 | `-` |
| test_wechat_tags | 认证 | 微信 | 正常：微信标签列表 | 正常流程 | `-` |
