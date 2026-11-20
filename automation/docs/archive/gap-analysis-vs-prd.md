# 测试缺口分析：PRD vs 现有用例

> 基于 PRD.md 第 6~11 节对比 `automation/testdata/cases/api-test-cases.xlsx` 现有 41 条用例
> 日期：2026-07-27

---

## 1. 我要摇人模块

### 1.1 功能点覆盖

| 功能点 | PRD 引用 | Excel 有? | Mock 有? | 优先级 |
|--------|----------|-----------|----------|--------|
| 对话 CRUD | 6.4.2 | ✅ 6 条 | ✅ | P0 |
| QA 同步问答 | 6.4.2 #1 | ✅ 1 条 | ✅ | P0 |
| QA 流式问答 | 6.4.2 #1 | ✅ 1 条 | ✅ | P0 |
| 我的工单列表 | 6.4.3 #1 | ✅ 1 条 | ✅ | P0 |
| 转工单 `POST /api/ai/qa/submit` | 6.4.1 #1 | ❌ | ❌ | **P0** |
| 确认派单 `POST /api/ai/qa/ticket/ack` | 6.4.3 #6 | ❌ | ❌ | **P0** |
| 催办 `POST /api/tasks/cuiban-notification` | 6.4.3 #7 | ❌ | ❌ | **P0** |
| 上报 `POST /api/tasks/cuiban-notification` | 6.4.3 #8 | ❌ | ❌ | **P0** |
| 升级选人 `GET /api/tasks/assignable-users` | 6.4.3 #9 | ❌ | ❌ | **P0** |
| 升级建单 `POST /api/tasks`(urgent) | 6.4.3 #9 | ❌ | ✅ | **P0** |
| 评论 `POST /api/tasks/{id}/comments` | 6.4.3 #10 | ✅ 1 条 | ✅ | P1 |
| 评论列表 `GET /api/tasks/{id}/comments` | 6.4.3 #10 | ❌ | ✅ | P1 |

### 1.2 业务流程

```
现有: 创建对话 → QA问答 → 消息 → 我的工单
缺少:              ↓
         AI诊断完成 → 转工单(submit) → 确认派单(ack)
               → 催办(cuiban-notification)
               → 升级(assignable-users → 建单)
               → 讨论(comments)
```

### 1.3 状态流转

现有 mock 状态机与 PRD 7.3 基本一致，无需调整。

### 1.4 权限控制

- 无认证测试：`/auth/login`、`/auth/me` 均无 Excel 用例（mock 已支持）
- 无 401 未认证场景

### 1.5 接口列表 — 缺口

| 接口 | 方法 | 缺? | 需扩展 Mock? |
|------|------|-----|-------------|
| /api/ai/qa/submit | POST | ❌ | ✅ 需新增 |
| /api/ai/qa/ticket/ack | POST | ❌ | ✅ 需新增 |
| /api/tasks/cuiban-notification | POST | ❌ | ✅ 需新增 |
| /api/tasks/assignable-users | GET | ❌ | ✅ 需新增 |
| /auth/login | POST | ❌ | ❌ mock 已有 |
| /auth/me | GET | ❌ | ❌ mock 已有 |

### 1.6 风险点 — 未覆盖

| 风险 | 等级 | 需用例? |
|------|------|---------|
| SSE 首字节延迟 | P0 | 性能测试，暂不纳入 |
| 转工单失败 | P0 | ✅ 需异常用例 |
| 催办通知缺参数 400 | P1 | ✅ 需边界用例 |
| 升级未选人拦截 | P1 | 前端用例，不纳入 |
| 评论超长 | P1 | ✅ 需边界用例 |

### 1.7 边界条件 — 未覆盖

| 场景 | 需用例? |
|------|---------|
| 无 token 访问催办 → 401 | ✅ |
| 催办缺 `assigned_to` → 400 | ✅ |
| 升级选人接口无结果 → 空数组 | ✅ |
| 评论内容超长 → 422 | ✅ |

---

## 2. 系统任务模块

### 2.1 功能点覆盖

| 功能点 | PRD 引用 | Excel 有? | Mock 有? | 优先级 |
|--------|----------|-----------|----------|--------|
| 工单 CRUD | 7.1 | ✅ 8 条 | ✅ | P0 |
| 状态流转 | 7.3 | ✅ 2 条 | ✅ | P0 |
| 指派 | 7.1 | ✅ 1 条 | ✅ | P0 |
| 筛选/统计 | 7.1 | ✅ 2 条 | ✅ | P1 |
| 评论 | 7.1 | ✅ 2 条 | ✅ | P1 |
| AI 指派 | 7.1 | ✅ 1 条 | ✅ | P2 |
| 多任务类型(bug/req/support) | 7.1 | ❌ | ✅ | P1 |
| 接单操作 | 7.1 | ❌ | ❌ | P1 |
| 转派操作 | 7.1 | ❌ | ❌ | P1 |
| AI 方案分析 `POST /api/ai/task/analyze` | 7.2 + 9.2 | ❌ | ✅ | P1 |
| AI 方案分析流式 `POST /api/ai/task/analyze/stream` | 7.2 + 9.2 | ❌ | ✅ | P1 |
| AI 方案提交 `POST /api/ai/task/submit` | 7.2 + 9.2 | ❌ | ❌ | P1 |
| AI 聊天 `POST /api/ai/task/chat` | 9.2 | ❌ | ❌ | P1 |
| AI 聊天流式 `POST /api/ai/task/chat/stream` | 9.2 | ❌ | ❌ | P1 |
| AI 任务列表 `POST /api/ai/task/list` | 9.2 | ❌ | ❌ | P1 |
| AI 健康检查 `GET /api/ai/task/health` | 9.2 | ❌ | ❌ | P1 |

### 2.2 业务流程

```
现有: 创建 → 列表 → 详情 → 更新 → 删除
         → 状态流转 → 指派 → 筛选 → 统计 → 评论 → AI指派
缺少:   → 接单 → 转派 → 上报
         → AI 方案分析(analyze) → 提交(submit)
```

### 2.3 状态流转

PRD 7.3: `待派单 → 已派单 → 处理中 → {待讨论/已上报/已解决 → 已关闭}`
现有 mock: `pending → in_progress → resolved → closed`
基本匹配，但缺少 `waiting` 和 `cancelled → in_progress` 重开场景。

### 2.4 权限控制

现有 mock 默认用户（admin/engineer/customer）支持三种角色，但未在 Excel 中构造角色差异测试。

### 2.5 接口列表 — 缺口

| 接口 | 方法 | 缺? | 需扩展 Mock? |
|------|------|-----|-------------|
| /api/ai/task/analyze | POST | ❌ | ❌ mock 已有 |
| /api/ai/task/analyze/stream | POST | ❌ | ❌ mock 已有 |
| /api/ai/task/submit | POST | ❌ | ✅ 需新增 |
| /api/ai/task/chat | POST | ❌ | ✅ 需新增 |
| /api/ai/task/chat/stream | POST | ❌ | ✅ 需新增 |
| /api/ai/task/list | POST | ❌ | ✅ 需新增 |
| /api/ai/task/health | GET | ❌ | ✅ 需新增 |
| /api/tasks/cuiban-notification | POST | ❌ | ✅ 需新增 |
| /api/tasks/assignable-users | GET | ❌ | ✅ 需新增 |

### 2.6 风险点 — 未覆盖

| 风险 | 等级 | 需用例? |
|------|------|---------|
| AI 方案分析 SSE 断连 | P1 | ✅ |
| 提交空方案 | P1 | ✅ |
| 非法状态流转(closed→in_progress) | P0 | ✅ 已有 |

### 2.7 边界条件 — 未覆盖

| 场景 | 需用例? |
|------|---------|
| 不存在的任务 ID 分析 → 404 | ✅ |
| 评论内容超长 → 422 | ✅ |
| 多任务类型字段差异 | ✅ |

---

## 3. 后台管理模块

### 3.1 功能点覆盖

| 功能点 | PRD 引用 | Excel 有? | Mock 有? | 优先级 |
|--------|----------|-----------|----------|--------|
| 工单总览 | 8.1 | ✅ 2 条 | ✅ | P0 |
| 项目列表/创建 | 8.1 | ✅ 2 条 | ✅ | P0 |
| 风险列表 | 8.1 | ✅ 1 条 | ✅ | P1 |
| 看板 | 8.1 | ✅ 1 条 | ✅ | P0 |
| 用户列表 | 8.1 | ⚠️ 只有list | ✅ | P1 |
| 角色列表 | 8.1 | ⚠️ 只有list | ✅ | P1 |
| 日报/周报 | 8.1 | ✅ 2 条 | ✅ | P1 |
| 导出 | 8.1 | ✅ 1 条 | ✅ | P2 |
| 资源 CRUD | 8.1 | ✅ 3 条 | ✅ | P1 |
| 用户 create/update/delete | 8.1 | ❌ | ❌ | P1 |
| 角色 create/update/delete | 8.1 | ❌ | ❌ | P1 |
| 项目详情 | 8.1 | ❌ | ❌ | P1 |
| 风险红黄灯 | 8.1 | ❌ | ❌ | P2 |
| 待审批上报 | 8.1 | ❌ | ❌ | P2 |
| AI 风险分析 | 8.1 | ❌ | ❌ | P2 |

### 3.2 ~ 3.7

后台管理模块现有 14 条用例覆盖了基础读取，主要缺口是用户/角色写操作和项目详情。

---

## 4. 全局模块 — 认证 + 微信

### 4.1 功能点覆盖

| 功能点 | Excel 有? | Mock 有? | 优先级 |
|--------|-----------|----------|--------|
| POST /auth/login | ❌ | ✅ | **P0** |
| GET /auth/me | ❌ | ✅ | **P0** |
| GET /api/wechat/health | ❌ | ✅ | P2 |
| GET /api/wechat/get_menu | ❌ | ✅ | P2 |
| POST /api/wechat/create_menu | ❌ | ✅ | P2 |
| POST /api/wechat/send_message | ❌ | ✅ | P2 |
| GET /api/wechat | ❌ | ✅ | P2 |
| POST /api/wechat | ❌ | ✅ | P2 |

认证接口（/auth/login、/auth/me）是**所有其他测试的前提**，虽然当前测试通过 mock fixture 绕过了认证（直接使用 mock_auth_header），但作为独立接口没有任何用例覆盖。

---

## 5. 优先级排序

| 优先级 | 模块 | 接口 | 说明 |
|--------|------|------|------|
| **P0** | 我要摇人 | POST /api/ai/qa/submit | 转工单核心链路 |
| **P0** | 我要摇人 | POST /api/ai/qa/ticket/ack | 确认派单 |
| **P0** | 我要摇人 | POST /api/tasks/cuiban-notification | 催办/上报 |
| **P0** | 我要摇人 | GET /api/tasks/assignable-users | 升级选人 |
| **P0** | 全局 | POST /auth/login | 认证基础 |
| **P0** | 全局 | GET /auth/me | 当前用户 |
| P1 | 系统任务 | POST /api/ai/task/analyze | AI 方案分析 |
| P1 | 系统任务 | POST /api/ai/task/submit | AI 方案提交 |
| P1 | 系统任务 | POST /api/ai/task/chat/stream | AI 聊天流式 |
| P1 | 后台管理 | 用户 CRUD | 用户管理完整 |
| P1 | 后台管理 | 角色 CRUD | 角色管理完整 |
| P2 | 全局 | /api/wechat/* | 微信 6 端点 |
