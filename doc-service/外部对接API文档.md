# USP测试管家 · 外部对接 API 文档

> 版本：v1（2026-09-18）｜适用对接方：外部软件后端
>
> 本文档描述对接方如何通过 HTTP API 管理 USP测试管家的用户，并实现免密跳转进入指定用户的测试界面。

## 1. 基础信息

| 项目 | 说明 |
|---|---|
| Base URL | `http://<测试管家地址>:8080`（默认端口 8080） |
| 协议 | HTTP，JSON（`Content-Type: application/json`） |
| 认证方式 | **admin scope license**（见下） |
| 健康检查 | `GET /api/health`（无需认证） |

## 2. 认证：admin scope license

- 由测试管家管理员发放（`POST /api/licenses`，scope=admin），形式为 `lic-` 开头的字符串，如 `lic-fb0cec100cc94c772907fe91d3cc6335`
- 在请求中通过 body 字段 `license`（POST）或 query 参数 `?license=`（GET/DELETE）传递
- **该 license 即管理凭据，务必只存放在对接方服务端，不得下发到浏览器或写进前端代码**
- 测试管家管理员可随时停用/删除该 license，立即断开联动
- admin scope license **不能**用于对话接口 `/api/external/chat`（会返回 403）

## 3. 接口总览

| # | 方法 | 路径 | 用途 |
|---|---|---|---|
| 1 | POST | `/api/external/users` | 创建用户 |
| 2 | GET | `/api/external/users` | 用户列表 |
| 3 | POST | `/api/external/users/password` | 修改用户密码 |
| 4 | POST | `/api/external/users/enabled` | 启用/禁用用户 |
| 5 | DELETE | `/api/external/users/{name}` | 删除用户 |
| 6 | POST | `/api/external/sso` | 签发一次性登录 token |
| 7 | POST | `/api/auth/sso` | 兑换 token（一般由测试管家前端自动调用） |

通用错误码：

| HTTP 码 | 含义 |
|---|---|
| 400 | 参数错误（用户名/密码格式、用户已存在、scope 非法、目标用户已被禁用等），`detail` 说明原因 |
| 401 | license 无效或已被停用；SSO token 无效/过期/已用过 |
| 403 | license 权限不足（非 admin scope）；试图操作管理员账号 |
| 404 | 目标用户不存在 |

---

## 4. 接口详情

### 4.1 创建用户 `POST /api/external/users`

**请求**

```json
{
  "license": "lic-xxxx",
  "username": "zhangsan",
  "password": "123456",
  "scope": "full"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| license | string | 是 | admin scope license |
| username | string | 是 | 2-32 位字母/数字/`_` `.` `-` |
| password | string | 是 | 至少 4 位 |
| scope | string | 否 | 该用户**绑定 license** 的权限范围：`full`（默认，可执行测试）/ `readonly`（只查询）。不允许 `admin` |

**响应 200**

```json
{
  "username": "zhangsan",
  "role": "user",
  "license_code": "lic-yyyy",
  "license_scope": "full"
}
```

说明：
- 创建的用户角色固定为普通用户（`user`），外部不能创建管理员
- 自动发放一个绑定该用户的 license（`license_code`），如需调用对话接口 `/api/external/chat` 可使用它

### 4.2 用户列表 `GET /api/external/users`

**请求**：`GET /api/external/users?license=lic-xxxx`

**响应 200**

```json
{
  "users": [
    {
      "username": "zhangsan",
      "role": "user",
      "enabled": true,
      "license_code": "lic-yyyy",
      "created": "2026-09-18T03:30:41.232237+00:00",
      "has_usp_default": false
    }
  ]
}
```

### 4.3 修改用户密码 `POST /api/external/users/password`

**请求**

```json
{
  "license": "lic-xxxx",
  "username": "zhangsan",
  "password": "newpass99"
}
```

**响应 200**：`{"updated": "zhangsan"}`

新密码至少 4 位。修改后该用户已登录的会话不受影响（下次登录用新密码）。

### 4.4 启用/禁用用户 `POST /api/external/users/enabled`

**请求**

```json
{
  "license": "lic-xxxx",
  "username": "zhangsan",
  "enabled": false
}
```

**响应 200**：`{"username": "zhangsan", "enabled": false}`

说明：禁用后该用户**立即**被踢出——已持有的登录 token 全部失效，其绑定 license 也随之失效。

### 4.5 删除用户 `DELETE /api/external/users/{name}`

**请求**：`DELETE /api/external/users/zhangsan?license=lic-xxxx`

**响应 200**：`{"deleted": "zhangsan"}`

说明（级联处理，无需额外操作）：
- 吊销该用户绑定的 license（对话接口立即 401）
- 清理其外部会话 key 映射
- 删除其加密存储的默认 USP 连接配置
- 历史测试会话保留（仅测试管家管理员可见，普通用户不可见，且永不与新用户混淆）

### 4.6 签发一次性登录 token `POST /api/external/sso`

**请求**

```json
{
  "license": "lic-xxxx",
  "username": "zhangsan"
}
```

**响应 200**

```json
{
  "sso_token": "sso-0591be79265f615295a95d6a918f7453bc9a1fd9bbb6fbf95b7cb72128258f32",
  "expires_in": 60,
  "username": "zhangsan"
}
```

**错误**：目标用户不存在 → 404；已被禁用 → 400；管理员账号 → 403。

**使用方式（对接方前端）**：拿到 `sso_token` 后**立即**在新窗口/新标签页打开：

```
http://<测试管家地址>:8080/?sso_token=<sso_token>
```

测试管家前端会自动完成登录，直接进入该用户的界面（无需密码），并把参数从地址栏清除。

**约束（重要）**：
- token **60 秒有效**，且**只能使用一次**（含失败场景）——必须"后端签发 → 前端马上打开"，不能预生成缓存
- 进入后的身份就是该用户，只能看到自己的测试会话记录（用户之间相互隔离）

### 4.7 兑换登录 token `POST /api/auth/sso`

通常由测试管家前端自动调用，对接方一般无需直接使用，仅供排查或服务端集成。

**请求**：`{"token": "sso-xxxx"}`

**响应 200**（与密码登录 `/api/auth/login` 同构）

```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "username": "zhangsan",
  "role": "user"
}
```

后续请求带 `Authorization: Bearer <JWT>` 即以该用户身份调用测试管家全部 Web API。

---

## 5. 对接流程小结

1. 测试管家管理员向对接方发放 admin scope license（`lic-xxxx`）
2. 对接方服务端保存该 license，封装上述接口
3. 用户同步：建用户（4.1）→ 日常改密/启停/删除（4.3–4.5）
4. 免密进入：用户在对接方软件点击按钮 → 对接方后端调 4.6 拿 `sso_token` → 前端新窗口打开 `http://<host>:8080/?sso_token=...`
5. 如需断开联动：测试管家管理员停用/删除该 license 即可

## 6. curl 示例（可直接测试）

```bash
BASE=http://localhost:8080
LIC=lic-xxxx    # 替换为实际 admin license

# 建用户
curl -s $BASE/api/external/users -H 'Content-Type: application/json' \
  -d "{\"license\":\"$LIC\",\"username\":\"zhangsan\",\"password\":\"123456\"}"

# 列表
curl -s "$BASE/api/external/users?license=$LIC"

# 改密
curl -s $BASE/api/external/users/password -H 'Content-Type: application/json' \
  -d "{\"license\":\"$LIC\",\"username\":\"zhangsan\",\"password\":\"newpass99\"}"

# 禁用 / 启用
curl -s $BASE/api/external/users/enabled -H 'Content-Type: application/json' \
  -d "{\"license\":\"$LIC\",\"username\":\"zhangsan\",\"enabled\":false}"

# 删除
curl -s -X DELETE "$BASE/api/external/users/zhangsan?license=$LIC"

# 签发 SSO token（返回的 sso_token 拼进 URL 立即打开）
curl -s $BASE/api/external/sso -H 'Content-Type: application/json' \
  -d "{\"license\":\"$LIC\",\"username\":\"zhangsan\"}"
```
