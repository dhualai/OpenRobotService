# 微信公众号带参数二维码

## 功能概述

后端新增「微信公众号带参数二维码」生成能力。浏览器访问 `GET /api/wechat/qrcode` 即可得到一张二维码图片；用户扫码后微信服务器会把场景值（scene_str）回调到 `/api/wechat`，后端据此触发客服消息跳转卡片，引导用户进入前端 `/app/call` 页面。

## 场景值（scene_str）

场景值是你传给微信、扫码后微信原样回传给后端的一个字符串标识（1~64 字符），用来区分不同二维码的来源。例如 `robot_2026_09_28`、`project_online_promo`。用户扫码时：

- **未关注** → 微信先引导关注，关注成功后推送 `subscribe` 事件，EventKey = 你的场景值
- **已关注** → 直接推送 `SCAN` 事件，EventKey = 你的场景值

## 接口

### 1. 生成二维码

```
GET /api/wechat/qrcode
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| scene | string | ✅ | - | 场景值（scene_str），扫码后微信通过 EventKey 回传 |
| permanent | bool | ❌ | false | 是否永久二维码（永久码最多 10 万个） |
| expire_seconds | int | ❌ | 2592000 | 临时码有效期（秒），最大 2592000（30 天） |
| as_json | bool | ❌ | false | 调试模式：true 时返回 JSON，否则直接返回图片 |

鉴权：`admin_auth`（DEBUG_MODE 下自动放行；生产环境需 `Authorization: Bearer <token>`）。

**返回（默认）：** `image/jpeg` 二进制，浏览器直接展示二维码。

**返回（as_json=true）：**

```json
{
  "success": true,
  "ticket": "gQH47joAAAAAAAAAASxodHRwOi8vd2VpeGlu...",
  "url": "http://weixin.qq.com/q/kZgfwMTm72WWPkovabbI",
  "expire_seconds": 2592000,
  "action_name": "QR_STR_SCENE",
  "scene_str": "robot_2026"
}
```

**示例：**

```
# 本地开发直接生成图片
http://localhost:8400/api/wechat/qrcode?scene=robot_2026

# 永久码 + 调试模式
http://localhost:8400/api/wechat/qrcode?scene=project_perm&permanent=true&as_json=true
```

### 2. 扫码回调（微信服务器 → 你的后端）

复用现有 `POST /api/wechat` 消息入口，新增对 `SCAN` 事件的处理，并在 `subscribe` 事件中解析 EventKey。

| 事件 | 触发条件 | 后端行为 |
|------|---------|---------|
| subscribe + EventKey 非空 | 未关注用户扫码 → 关注 | 回欢迎语文本 + 推送跳转卡片 |
| subscribe + EventKey 为空 | 用户从公众号菜单/搜索直接关注 | 回欢迎语文本 + 推送个人中心卡片（原有逻辑） |
| SCAN | 已关注用户扫码 | 回空 XML + 推送跳转卡片 |

跳转卡片 URL：`{FRONTEND_BASE_URL}/app/call?scene={scene_str}&openid={openid}`

## 实现细节

### 文件改动

| 文件 | 改动 |
|------|------|
| `backend/app/core/config.py` | 新增 `WECHAT_QRCODE_CREATE_URL` 属性 |
| `backend/app/wechat/services/wechat_service.py` | 新增 `create_qrcode_ticket()` + `get_qrcode_image_bytes()`，均带 access_token 失效自动重试 |
| `backend/app/wechat/api/wechat.py` | 新增 `GET /qrcode` 路由 + `handle_scan_event()` + `_send_scan_redirect_card()`；`handle_subscribe_event()` 增加 EventKey 解析 |

### 微信 API 链路

```
后端 ── POST /cgi-bin/qrcode/create ──→ 微信
     ←─ {ticket, expire_seconds, url} ──

后端 ── GET /cgi-bin/showqrcode?ticket=xxx ──→ 微信
     ←─ image/jpeg ──

用户扫码 → 微信 POST /api/wechat (XML, EventKey=scene_str) → 后端
                                                              │
                                                              ├── subscribe 事件
                                                              └── SCAN 事件
                                                                      │
                                                                      ▼
                                                         _send_scan_redirect_card()
                                                         → 客服消息推图文卡片
                                                         → 用户在微信里点开
                                                         → 微信内置浏览器打开 /app/call
```

### 关键配置项

| 配置 | 位置 | 说明 |
|------|------|------|
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | `backend/.env` | 公众号凭证（你已配好） |
| `FRONTEND_BASE_URL` | `backend/.env` | 生产环境必须是前端真实域名，不然跳转卡片里的链接用户点不开 |

## 测试方法

### 阶段 1：只测生成（本地，不需要外网）

```powershell
cd backend
python main.py
```

```
# 先试 JSON 排查
http://localhost:8400/api/wechat/qrcode?scene=test_001&as_json=true

# 再试图片
http://localhost:8400/api/wechat/qrcode?scene=test_001
```

### 阶段 2：测扫码回调（需要外网 + 线上部署）

1. 部署新代码到外网服务器
2. 用手机微信扫阶段 1 生成的二维码
3. 看后端日志应出现：
   ```
   已关注用户 oXk4js1234567890abcdefg 扫码, EventKey=test_001
   推送扫码跳转卡片: openid=oXk4js1234567890abcdefg, scene=test_001, url=https://你的前端域名/app/call?scene=test_001&openid=oXk4js1234567890abcdefg
   ```
4. 微信对话里应收到图文卡片，点开进入 `/app/call?scene=test_001&openid=xxx`

## 注意事项

- **永久码最多 10 万个**，先拿临时码调通再决定是否换永久
- **客服消息限制**：用户 48 小时内和公众号有过交互才能发，扫码本身满足条件
- **公众号必须认证**才能用带参数二维码接口
- 扫码后**不能自动打开浏览器**——微信客户端安全限制，只能推卡片让用户点一下跳转
