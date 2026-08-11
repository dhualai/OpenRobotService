# Backend API 清单与权限分析

## 认证机制概述

系统使用 **JWT Bearer Token** 作为主要认证方式，存在于两套独立的认证实现：
- [auth_routes.py](file:///d:/CODE/7_18/OpenRobotService/backend/app/core/auth_routes.py) — 核心认证（`/api/auth/*`）
- [auth.py](file:///d:/CODE/7_18/OpenRobotService/backend/app/modules/admin/api/auth.py) — admin 模块认证（`/api/admin/auth/*`）

**关键发现**：[config.py](file:///d:/CODE/7_18/OpenRobotService/backend/app/modules/admin/utils_das/config.py#L6) 中 `DEBUG_MODE = True`，这意味着所有使用 `Depends(security if not DEBUG_MODE else lambda: None)` 的接口在 DEBUG 模式下**完全跳过认证**。

### 权限检查方式

| 方式 | 说明 |
|------|------|
| `get_current_active_user_from_token` | 通用依赖，解析 JWT Token 获取用户信息（返回 Dict） |
| `require_permission("code:here")` | 权限码检查，支持 `*` 通配符和 `admin` 超级权限 |
| `get_current_admin_user` | 要求 `admin` 权限 |
| `Depends(security)` 即 `HTTPBearer()` | 仅验证 Token 有效性 |
| `X-API-Key` Header | 外部同步接口专用 |

---

## 一、公开接口（无需认证）

| 方法 | 路径 | 说明 | 文件 |
|------|------|------|------|
| GET | `/api/health` | 服务健康检查 | [__init__.py#L85](file:///d:/CODE/7_18/OpenRobotService/backend/app/__init__.py#L85) |
| POST | `/api/auth/register` | 用户注册 | [auth_routes.py#L89](file:///d:/CODE/7_18/OpenRobotService/backend/app/core/auth_routes.py#L89) |
| POST | `/api/auth/login` | 用户登录（返回 JWT） | [auth_routes.py#L140](file:///d:/CODE/7_18/OpenRobotService/backend/app/core/auth_routes.py#L140) |
| POST | `/api/auth/refresh` | 刷新访问令牌 | [auth_routes.py#L148](file:///d:/CODE/7_18/OpenRobotService/backend/app/core/auth_routes.py#L148) |
| POST | `/api/admin/auth/register` | admin 模块用户注册 | [auth.py#L92](file:///d:/CODE/7_18/OpenRobotService/backend/app/modules/admin/api/auth.py#L92) |
| POST | `/api/admin/auth/login` | admin 模块用户登录 | [auth.py#L144](file:///d:/CODE/7_18/OpenRobotService/backend/app/modules/admin/api/auth.py#L144) |
| POST | `/api/admin/auth/refresh` | admin 模块刷新令牌 | [auth.py#L152](file:///d:/CODE/7_18/OpenRobotService/backend/app/modules/admin/api/auth.py#L152) |
| GET | `/api/wechat` | 微信服务器签名验证 | [wechat.py#L69](file:///d:/CODE/7_18/OpenRobotService/backend/app/wechat/api/wechat.py#L69) |
| POST | `/api/wechat` | 微信消息回调接收 | [wechat.py#L93](file:///d:/CODE/7_18/OpenRobotService/backend/app/wechat/api/wechat.py#L93) |
| GET | `/api/wechat/health` | 微信模块健康检查 | [health.py#L10](file:///d:/CODE/7_18/OpenRobotService/backend/app/wechat/api/health.py#L10) |

---

## 二、需要 JWT 认证的接口

### 2.1 认证信息接口

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/auth/me` | `get_current_active_user_from_token` | 获取当前用户信息（核心模块） |
| GET | `/api/admin/auth/me` | `get_current_active_user_from_token` | 获取当前用户信息（admin 模块） |

### 2.2 Admin - 项目管理（`/api/admin/projects`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/projects/` | `security` (DEBUG 下跳过) | 获取项目列表 |
| GET | `/api/admin/projects/me` | 手动解析 JWT | 获取当前用户关联项目 |
| GET | `/api/admin/projects/{project_id}` | `security` (DEBUG 下跳过) | 获取单个项目 |
| POST | `/api/admin/projects/` | `security` (DEBUG 下跳过) | 创建项目 |
| PUT | `/api/admin/projects/{project_id}` | `security` (DEBUG 下跳过) | 更新项目 |
| DELETE | `/api/admin/projects/{project_id}` | `security` (DEBUG 下跳过) | 删除项目 |
| POST | `/api/admin/projects/licenses` | `security` (DEBUG 下跳过) | 创建项目授权 |
| GET | `/api/admin/projects/licenses/{project_code}` | `security` (DEBUG 下跳过) | 获取项目授权信息 |

> **项目唯一键约束**：创建（POST）与更新（PUT）项目时，`project_code`/`name` 均须全局唯一。
> 已存在其他项目占用时返回 `409 Conflict`，`detail` 形如 `项目编号「xxx」已存在，请重新输入`；并发命中数据库唯一索引也统一转为 409。

### 2.3 Admin - 风险管理（`/api/admin/projects/risks`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/projects/risks/` | `security` (DEBUG 下跳过) | 获取风险列表 |
| POST | `/api/admin/projects/risks/` | `security` (DEBUG 下跳过) | 新增风险 |
| GET | `/api/admin/projects/risks/{risk_code}` | `security` (DEBUG 下跳过) | 获取单个风险详情 |
| PUT | `/api/admin/projects/risks/{risk_code}` | `security` (DEBUG 下跳过) | 更新风险信息 |
| DELETE | `/api/admin/projects/risks/{risk_code}` | `security` (DEBUG 下跳过) | 删除风险 |
| GET | `/api/admin/projects/risks/filters` | `security` (DEBUG 下跳过) | 获取过滤器选项 |

### 2.4 Admin - 日报管理（`/api/admin/daily-reports`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/daily-reports/` | `security` (DEBUG 下跳过) | 获取日报列表 |
| GET | `/api/admin/daily-reports/{report_id}` | `security` (DEBUG 下跳过) | 获取单个日报 |
| GET | `/api/admin/daily-reports/by-date/{project_code}/{report_date}` | `security` (DEBUG 下跳过) | 按日期获取日报 |
| POST | `/api/admin/daily-reports/` | `security` (DEBUG 下跳过) | 创建日报 |
| PUT | `/api/admin/daily-reports/{report_id}` | `security` (DEBUG 下跳过) | 更新日报 |
| DELETE | `/api/admin/daily-reports/{report_id}` | `security` (DEBUG 下跳过) | 删除日报 |
| GET | `/api/admin/daily-reports/search/{keyword}` | `security` (DEBUG 下跳过) | 搜索日报 |

### 2.5 Admin - 数据导出（`/api/admin/export`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/admin/export/project/{project_code}` | `security` (DEBUG 下跳过) | 导出项目数据（gzip） |
| POST | `/api/admin/export/apply_project_license` | `security` (DEBUG 下跳过) | 申请项目授权（MQTT） |

### 2.6 Admin - 用户管理（`/api/admin/users`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/users/` | `require_permission("backend:user:base:read")` | 获取用户列表 |
| POST | `/api/admin/users/` | `require_permission("backend:user:base:write")` | 创建用户 |
| GET | `/api/admin/users/{username}/detail` | `get_current_active_user_from_token` | 获取用户详情 |
| PUT | `/api/admin/users/{username}` | `get_current_active_user_from_token` + 业务权限校验 | 更新用户（可更新自己，管理员可更新所有人） |
| DELETE | `/api/admin/users/{username}` | `require_permission("backend:user:base:delete")` | 删除用户 |
| POST | `/api/admin/users/{username}/roles` | `require_permission("backend:user:role_project:write")` | 为用户分配角色 |
| POST | `/api/admin/users/{username}/roles/remove` | `require_permission("backend:user:role_project:write")` | 批量移除用户角色 |
| GET | `/api/admin/users/{username}/reporters` | `require_permission("backend:user:base:read")` | 获取用户汇报人列表 |
| POST | `/api/admin/users/{username}/uspinfo` | `get_current_active_user_from_token` + 业务权限校验 | 更新 USP 账户信息 |
| POST | `/api/admin/users/project/assign-roles` | `require_permission("backend:user:role_project:write")` | 批量分配项目角色 |

### 2.7 Admin - 角色管理（`/api/admin/roles`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/roles/` | `require_permission("backend:role:base:read")` | 获取所有角色 |
| POST | `/api/admin/roles/` | `require_permission("backend:role:base:write")` | 创建新角色 |
| GET | `/api/admin/roles/{role_id}/permissions` | `require_permission("backend:role:base:read")` | 获取角色权限 |
| POST | `/api/admin/roles/{role_id}/permissions` | `require_permission("backend:role:permission:write")` | 给角色增加权限 |
| GET | `/api/admin/roles/{role_id}/all-permissions` | `require_permission("backend:role:base:read")` | 获取角色所有权限 |
| DELETE | `/api/admin/roles/{role_id}/permissions` | `require_permission("backend:role:permission:write")` | 从角色移除权限 |
| POST | `/api/admin/roles/{role_id}/permissions/remove` | `require_permission("backend:role:permission:write")` | 批量删除角色权限 |
| DELETE | `/api/admin/roles/{role_id}` | `require_permission("backend:role:base:delete")` | 删除角色 |

### 2.8 Admin - 权限管理（`/api/admin/permissions`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/permissions/` | `require_permission("backend:permission:base:read")` | 获取所有权限 |
| GET | `/api/admin/permissions/{permission_id}` | `get_current_admin_user`（需 admin） | 获取指定权限详情 |
| POST | `/api/admin/permissions/` | `require_permission("backend:permission:base:write")` | 创建权限 |
| PUT | `/api/admin/permissions/{permission_id}` | `require_permission("backend:permission:base:write")` | 更新权限 |
| DELETE | `/api/admin/permissions/{permission_id}` | `require_permission("backend:permission:base:delete")` | 删除权限 |

### 2.9 Admin - 工单代理（`/api/admin/tickets`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/tickets/` | `security` (DEBUG 下跳过) | 代理 AI 服务获取工单列表 |
| GET | `/api/admin/tickets/stats` | `security` (DEBUG 下跳过) | 工单状态统计 |

### 2.10 Admin - 仪表盘（`/api/admin/dashboard`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/dashboard/tickets/summary` | **无显式认证** ⚠️ | 工单状态汇总 |
| GET | `/api/admin/dashboard/tickets` | **无显式认证** ⚠️ | 按状态筛选工单 |
| GET | `/api/admin/dashboard/projects/summary` | **无显式认证** ⚠️ | 项目阶段汇总 |
| GET | `/api/admin/dashboard/projects/urgency` | **无显式认证** ⚠️ | 项目紧急度汇总 |
| GET | `/api/admin/dashboard/projects` | **无显式认证** ⚠️ | 按阶段/紧急度筛选项目 |

### 2.11 Admin - 资源管理（`/api/admin/resource-manager`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/resource-manager/resources/` | **无显式认证** ⚠️ | 获取资源列表 |
| GET | `/api/admin/resource-manager/resources/recent` | **无显式认证** ⚠️ | 获取最近资源 |
| GET | `/api/admin/resource-manager/resources/stats/summary` | **无显式认证** ⚠️ | 资源统计 |
| GET | `/api/admin/resource-manager/resources/stats/daily` | **无显式认证** ⚠️ | 资源按日统计 |
| GET | `/api/admin/resource-manager/resources/{resource_id}` | **无显式认证** ⚠️ | 获取单个资源 |
| GET | `/api/admin/resource-manager/resources/hash/{hash_code}` | **无显式认证** ⚠️ | 按 hash 查资源 |
| GET | `/api/admin/resource-manager/resources/owner/{owner_id}` | **无显式认证** ⚠️ | 按拥有者查资源 |
| GET | `/api/admin/resource-manager/resources/type/{resource_type}` | **无显式认证** ⚠️ | 按类型查资源 |
| GET | `/api/admin/resource-manager/resources/category/{category}` | **无显式认证** ⚠️ | 按分类查资源 |
| GET | `/api/admin/resource-manager/resources/search/query` | **无显式认证** ⚠️ | 搜索资源 |
| POST | `/api/admin/resource-manager/resources/` | **无显式认证** ⚠️ | 创建资源（上传） |
| PUT | `/api/admin/resource-manager/resources/{resource_id}` | **无显式认证** ⚠️ | 更新资源 |
| DELETE | `/api/admin/resource-manager/resources/{resource_id}` | **无显式认证** ⚠️ | 删除资源 |
| POST | `/api/admin/resource-manager/resources/{resource_id}/download-count` | **无显式认证** ⚠️ | 下载计数 |
| GET | `/api/admin/resource-manager/resources/{resource_id}/download` | **无显式认证** ⚠️ | 代理下载资源 |
| GET | `/api/admin/resource-manager/resources/{resource_id}/download-url` | **无显式认证** ⚠️ | 获取预签名下载 URL |
| GET | `/api/admin/resource-manager/resources/{resource_id}/thumbnail-url` | **无显式认证** ⚠️ | 获取缩略图 URL |
| GET | `/api/admin/resource-manager/resources/{resource_id}/preview-url` | **无显式认证** ⚠️ | 获取预览 URL |
| POST | `/api/admin/resource-manager/resources/{resource_id}/like` | **无显式认证** ⚠️ | 点赞 |
| POST | `/api/admin/resource-manager/resources/sync-build-deploy` | **无显式认证** ⚠️ | 同步构建部署 |
| POST | `/api/admin/resource-manager/resources/sync-oss` | **无显式认证** ⚠️ | 同步 OSS 资源 |
| GET | `/api/admin/resource-manager/resource-folders/` | **无显式认证** ⚠️ | 获取文件夹列表 |
| GET | `/api/admin/resource-manager/resource-folders/root` | **无显式认证** ⚠️ | 获取根文件夹 |
| GET | `/api/admin/resource-manager/resource-folders/root/children` | **无显式认证** ⚠️ | 获取根文件夹子项 |
| GET | `/api/admin/resource-manager/resource-folders/{folder_id}` | **无显式认证** ⚠️ | 获取单个文件夹 |
| GET | `/api/admin/resource-manager/resource-folders/{folder_id}/children` | **无显式认证** ⚠️ | 获取文件夹子项 |
| POST | `/api/admin/resource-manager/resource-folders/` | **无显式认证** ⚠️ | 创建文件夹 |
| PUT | `/api/admin/resource-manager/resource-folders/{folder_id}` | **无显式认证** ⚠️ | 更新文件夹 |
| DELETE | `/api/admin/resource-manager/resource-folders/{folder_id}` | **无显式认证** ⚠️ | 删除文件夹 |
| GET | `/api/admin/resource-manager/minio/presigned-url` | **无显式认证** ⚠️ | 获取 MinIO 预签名 URL |

### 2.11 Admin - 通知（`/api/wechat/backend/notify`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/wechat/backend/notify/` | `security` (DEBUG 下跳过) | 发送通知 |

### 2.12 Tasks - 任务管理（`/api/tasks`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/tasks/` | `get_current_active_user_from_token` | 创建任务 |
| GET | `/api/tasks/` | **手动取 Token** ⚠️ | 获取任务列表 |
| POST | `/api/tasks/filter` | **手动取 Token** ⚠️ | 复合过滤查询 |
| GET | `/api/tasks/stats/overview` | `get_current_active_user_from_token` | 任务统计概览 |
| GET | `/api/tasks/{task_id}` | **手动取 Token** ⚠️ | 获取任务详情 |
| PUT | `/api/tasks/{task_id}` | `get_current_active_user_from_token` + 状态机业务校验 | 更新任务 |
| DELETE | `/api/tasks/{task_id}` | `get_current_active_user_from_token` + 业务校验 | 删除任务 |
| POST | `/api/tasks/{task_id}/comments` | `get_current_active_user_from_token` | 添加评论 |
| GET | `/api/tasks/{task_id}/comments` | **手动取 Token** ⚠️ | 获取评论列表 |
| PUT | `/api/tasks/comments/{comment_id}` | `get_current_active_user_from_token` + 本人校验 | 更新评论 |
| DELETE | `/api/tasks/comments/{comment_id}` | `get_current_active_user_from_token` + 本人校验 | 删除评论 |
| PATCH | `/api/tasks/{task_id}/status` | `get_current_active_user_from_token` + 业务校验 | 更新任务状态 |
| PATCH | `/api/tasks/{task_id}/assign` | `get_current_active_user_from_token` + admin 校验 | 分配任务 |
| POST | `/api/tasks/{task_id}/ai-assign` | `get_current_active_user_from_token` | 触发 AI 分配 |
| POST | `/api/tasks/comments/attachments` | `get_current_active_user_from_token` | 上传评论附件 |
| POST | `/api/tasks/comments/attachments/delete` | `get_current_active_user_from_token` | 删除附件 |
| POST | `/api/tasks/cuiban-notification` | `get_current_active_user_from_token` | 发送催办通知 |
| GET | `/api/tasks/assignable-users` | `get_current_active_user_from_token` | 可指派人员列表 |

### 2.13 Tasks - 异步任务（`/api/tasks`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/tasks/tasks` | **无显式认证** ⚠️ | 创建异步任务 |
| GET | `/api/tasks/tasks/{task_id}` | **无显式认证** ⚠️ | 查询异步任务状态 |

### 2.14 Call - AI 问答（`/api/call/qa`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/call/qa/ask` | **无显式认证** ⚠️ | 提问接口 |
| POST | `/api/call/qa/ask/stream` | **无显式认证** ⚠️ | 流式提问 |

### 2.15 Call - 会话管理（`/api/call/conversations`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/call/conversations` | `get_current_active_user_from_token` | 创建会话 |
| GET | `/api/call/conversations/{conversation_id}` | **无显式认证** ⚠️ | 获取会话详情（含消息） |
| GET | `/api/call/conversations` | `get_current_active_user_from_token` | 获取会话列表 |
| PUT | `/api/call/conversations/{conversation_id}` | **无显式认证** ⚠️ | 更新会话 |
| DELETE | `/api/call/conversations/{conversation_id}` | **无显式认证** ⚠️ | 删除会话 |

### 2.16 Call - 消息管理（`/api/call/messages`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/call/messages` | **无显式认证** ⚠️ | 创建消息 |
| GET | `/api/call/messages/{message_id}` | **无显式认证** ⚠️ | 获取消息 |
| GET | `/api/call/messages` | **无显式认证** ⚠️ | 获取会话消息列表 |
| GET | `/api/call/messages/{conversation_id}/brief` | **无显式认证** ⚠️ | 获取消息概览 |
| PUT | `/api/call/messages/{message_id}` | **无显式认证** ⚠️ | 更新消息 |
| DELETE | `/api/call/messages/{message_id}` | **无显式认证** ⚠️ | 删除消息 |

### 2.17 Call - 我的任务（`/api/call/my-tasks`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/call/my-tasks/` | **手动取 Token** ⚠️ | 获取我的任务列表 |
| GET | `/api/call/my-tasks/{task_id}` | **手动取 Token** + 本人校验 ⚠️ | 获取我的任务详情 |
| POST | `/api/call/my-tasks/` | **手动取 Token** ⚠️ | 创建任务（报障） |

### 2.18 Call - AAS 用户接口（`/api/call/user`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/call/user/login` | 调用外部 AAS | 登录（代理到 AAS 服务） |
| POST | `/api/call/user/refresh` | 调用外部 AAS | 刷新令牌 |
| GET | `/api/call/user/me` | `Authorization` Header | 获取当前用户信息 |

### 2.19 WeChat - 微信功能（`/api/wechat`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/wechat/login` | 无（openid 作为参数） | 微信用户登录 |
| GET | `/api/wechat/permissions` | 无（openid 作为参数） | 获取微信用户权限 |
| GET | `/api/wechat/callback` | OAuth 回调 | 微信授权回调 |
| GET | `/api/wechat/get-openid` | 无 | 用 code 换 openid |
| GET | `/api/wechat/config/js-sdk-config` | 无 | 获取 JS-SDK 配置 |
| POST | `/api/wechat/import-data` | `admin_auth`（DEBUG 下跳过） | 数据导入 |

### 2.20 WeChat - 消息管理（`/api/wechat`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/wechat/send_message` | `admin_auth` | 发送消息 |
| POST | `/api/wechat/broadcast_message` | `admin_auth` | 广播消息 |
| POST | `/api/wechat/send_link_message` | `admin_auth` | 发送链接消息 |
| POST | `/api/wechat/webnotify` | `admin_auth` | 网页通知（内部调用） |

### 2.21 WeChat - 菜单管理（`/api/wechat`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/wechat/create_menu` | `admin_auth` | 创建微信菜单 |
| GET | `/api/wechat/get_menu` | `admin_auth` | 获取微信菜单 |
| DELETE | `/api/wechat/delete_menu` | `admin_auth` | 删除微信菜单 |
| POST | `/api/wechat/create_conditional_menu` | `admin_auth` | 创建个性化菜单 |
| POST | `/api/wechat/create_conditional_menu_from_file` | `admin_auth` | 从文件创建个性化菜单 |
| DELETE | `/api/wechat/delete_conditional_menu/{menuid}` | `admin_auth` | 删除个性化菜单 |
| POST | `/api/wechat/try_match_menu` | `admin_auth` | 测试菜单匹配 |

### 2.22 WeChat - 标签管理（`/api/wechat/tag`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/wechat/tag` | `admin_auth` | 获取所有标签 |
| POST | `/api/wechat/tag` | `admin_auth` | 创建标签 |
| PUT | `/api/wechat/tag/{tag_id}` | `admin_auth` | 更新标签 |
| DELETE | `/api/wechat/tag/{tag_id}` | `admin_auth` | 删除标签 |
| POST | `/api/wechat/tag/batch-tagging` | `admin_auth` | 批量打标签 |
| POST | `/api/wechat/tag/batch-untagging` | `admin_auth` | 批量取消标签 |
| GET | `/api/wechat/tag/{tag_id}/fans` | `admin_auth` | 获取标签下粉丝 |
| GET | `/api/wechat/tag/user/{openid}` | `admin_auth` | 获取用户标签 |

### 2.23 WeChat - 调试（`/api/wechat`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| POST | `/api/wechat/debug` | `admin_auth` | 调试请求转发 |

### 2.24 外部任务源集成（`/api/tasks/sources`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/tasks/sources` | `X-API-Key` Header | 列出已注册任务源 |
| POST | `/api/tasks/sources/{source}/sync` | `X-API-Key` Header | 触发任务源同步 |
| POST | `/api/tasks/sources/wecom/projects/sync` | `get_current_active_user_from_token` | 同步企业微信项目 |

### 2.25 账号映射管理（`/api/admin/task-user-mappings`）

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|----------|------|
| GET | `/api/admin/task-user-mappings` | `get_current_active_user_from_token` | 列出映射 |
| POST | `/api/admin/task-user-mappings` | `get_current_active_user_from_token` | 创建映射 |
| PUT | `/api/admin/task-user-mappings/{mapping_id}` | `get_current_active_user_from_token` | 更新映射 |
| DELETE | `/api/admin/task-user-mappings/{mapping_id}` | `get_current_active_user_from_token` | 删除映射 |

---

## 三、权限码字典

从代码中提取的权限码模式：

| 权限码 | 说明 |
|--------|------|
| `admin` | 超级管理员（绕过所有权限检查） |
| `user` | 普通用户（注册时默认分配） |
| `backend:user:base:read` | 读取用户基础信息 |
| `backend:user:base:write` | 创建/写入用户 |
| `backend:user:base:delete` | 删除用户 |
| `backend:user:role_project:write` | 分配项目角色 |
| `backend:role:base:read` | 读取角色 |
| `backend:role:base:write` | 创建/更新角色 |
| `backend:role:base:delete` | 删除角色 |
| `backend:role:permission:write` | 管理角色权限 |
| `backend:permission:base:read` | 读取权限定义 |
| `backend:permission:base:write` | 创建/更新权限定义 |
| `backend:permission:base:delete` | 删除权限定义 |

---

## 四、安全风险提示 ⚠️

1. **DEBUG_MODE 全模块开启**：[config.py#L6](file:///d:/CODE/7_18/OpenRobotService/backend/app/modules/admin/utils_das/config.py#L6) 设置 `DEBUG_MODE = True`，所有 `security` 依赖被跳过，**所有用户管理、项目管理、风险、日报、导出、工单、通知接口在 DEBUG 模式下无需认证即可访问**。

2. **无显式认证的接口**：Dashboard 模块（`/api/admin/dashboard/*`）、资源管理模块（`/api/admin/resource-manager/*`）、Call 模块的会话/消息/QA 接口、Tasks 异步任务接口等**未添加任何认证依赖**，任何人可直接访问。

3. **手动 Token 解析不一致**：多个接口（如 tasks 的 GET 列表、call/my-tasks）在函数内部手动 `request.headers.get("Authorization")` 取 Token 并自行解析，而非使用统一的 `get_current_active_user_from_token` 依赖，存在不一致的认证逻辑。

4. **两套独立认证系统**：`app/core/auth_routes.py` 和 `app/modules/admin/api/auth.py` 各自实现了相同的 `get_current_active_user_from_token` 和 `require_permission`，逻辑重复。

5. **wechat 模块的 `admin_auth`**：实际等于 `Depends(security)`（在 DEBUG 下跳过），但 production 环境下需要有效的 JWT Token。

6. **X-API-Key 保护不足**：仅用于外部任务源同步接口，key 通过环境变量配置，但代码中存在硬编码默认值。
        
            
toolName: todo_write
            
status: success
          
Todos updated: 4 items
          
以上就是完整的 Backend API 清单及权限分析。总结要点：

- **共 7 大路由模块**，约 **90+ 个 API 端点**
- **公开接口**约 10 个（健康检查、注册、登录、微信回调等）
- **需要认证的接口**使用 JWT Bearer Token，通过 `get_current_active_user_from_token` 或 `require_permission` 依赖实现
- **DEBUG_MODE = True** 导致 admin 模块下大部分接口的 `HTTPBearer` 认证被跳过，这是当前最大的安全隐患
- **约 30+ 个接口未添加任何认证依赖**（Dashboard、Resource Manager、Call 子模块等），存在越权风险
- 项目采用基于 RBAC 的权限模型，权限码格式为 `模块:资源:动作`，支持 `*` 通配符和 `admin` 超级权限绕过