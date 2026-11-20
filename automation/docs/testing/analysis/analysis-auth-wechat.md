# 认证与微信模块 - 业务分析

## 1. 功能点

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 用户登录 | 用户名密码登录 `POST /auth/login` | P0 |
| 当前用户 | 获取当前用户信息 `GET /auth/me` | P0 |
| 微信健康检查 | `GET /api/wechat/health` | P2 |
| 获取菜单 | `GET /api/wechat/get_menu` | P2 |
| 创建菜单 | `POST /api/wechat/create_menu` | P2 |
| 发送消息 | `POST /api/wechat/send_message` | P2 |
| 微信验证 GET | `GET /api/wechat` 验证服务器 | P2 |
| 微信回调 POST | `POST /api/wechat` 消息回调 | P2 |

## 2. 业务流程

```
登录流程:
  用户 -> POST /auth/login {username, password} -> 返回 token
  -> GET /auth/me (Authorization: Bearer token) -> 返回用户信息

微信流程:
  微信用户 -> 扫码关注 -> POST /api/wechat 回调
  -> 发送消息 -> GET /api/wechat/get_menu
  -> 管理员 -> POST /api/wechat/create_menu
  -> 主动推送 -> POST /api/wechat/send_message
```

## 3. 状态流转

认证模块使用 JWT Token：
- token 有效期：24h（默认）
- token 过期后需要重新登录
- token 认证中间件拦截所有需要认证的 API

微信模块无状态机。

## 4. 权限控制

| 接口 | 权限要求 |
|------|---------|
| POST /auth/login | 无需认证 |
| GET /auth/me | 需有效 JWT Token |
| /api/wechat/* | 需微信签名校验（非 JWT） |

## 5. 接口列表

| 方法 | 路径 | 说明 | 版本 | Mock 已有? |
|------|------|------|------|-----------|
| POST | /auth/login | 登录 | v1 | ✅ |
| GET | /auth/me | 当前用户 | v1 | ✅ |
| GET | /api/wechat/health | 健康检查 | v1 | ✅ |
| GET | /api/wechat/get_menu | 获取菜单 | v1 | ✅ |
| POST | /api/wechat/create_menu | 创建菜单 | v1 | ✅ |
| POST | /api/wechat/send_message | 发送消息 | v1 | ✅ |
| GET | /api/wechat | 微信验证 | v1 | ✅ |
| POST | /api/wechat | 微信回调 | v1 | ✅ |

Mock 默认用户：

| 用户名 | 密码 | 角色 |
|--------|------|------|
| testadmin | admin123 | admin |
| engineer | eng123 | engineer |
| customer | cust123 | customer |

## 6. 风险点

| 风险 | 等级 | 影响 | 建议处理 |
|------|------|------|---------|
| 密码暴力破解 | P0 | 账号泄露 | 登录失败限流+锁定 |
| token 泄露 | P0 | 冒用身份 | token 绑定设备/IP |
| token 过期处理不当 | P1 | 用户被迫重新登录 | 自动刷新 token |
| 微信签名校验失败 | P2 | 微信回调被拦截 | 详细错误日志 |
| 微信菜单更新不及时 | P2 | 用户看到旧菜单 | 缓存策略 |

## 7. 边界条件

| 条件 | 说明 | 预期行为 |
|------|------|---------|
| 密码错误 | 错误密码 | 401 |
| 用户名不存在 | 未注册用户 | 401 |
| 空密码 | password="" | 422 |
| token 过期 | 过期 JWT | 401 |
| token 格式错误 | 非法 JWT | 401 |
| 无 token | 不传 Authorization | 401 |
| 微信签名参数缺失 | 无 signature/timestamp/nonce | 403 |
| 微信消息内容为空 | 空消息 | 200（无操作） |
