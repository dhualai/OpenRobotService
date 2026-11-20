# 我要摇人模块 - 业务分析

## 1. 功能点

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 对话 CRUD | 创建/列表/详情会话 | P0 |
| QA 同步问答 | 提交问题并获取 AI 回答 | P0 |
| QA 流式问答 | SSE 实时流式回答 | P0 |
| 转工单 | AI 诊断完成后提交工单 `POST /api/ai/qa/submit` | P0 |
| 确认派单 | 客户确认派单 `POST /api/ai/qa/ticket/ack` | P0 |
| 催办 | 催办已分配工单 `POST /api/tasks/cuiban-notification` | P0 |
| 升级选人 | 获取可选择人员列表 `GET /api/tasks/assignable-users` | P0 |
| 升级建单 | 升级后建单 `POST /api/tasks`(urgent) | P0 |
| 消息创建 | 在会话中发送消息 | P1 |
| 消息列表 | 查看会话消息列表 | P1 |
| 我的工单列表 | 查看当前用户工单 | P1 |
| 我的工单创建 | 创建新工单 | P1 |
| 评论 | 添加工单评论 | P1 |
| 评论列表 | 查看工单评论列表 | P1 |
| 登录 | 用户密码登录 `POST /auth/login` | P0 |
| 当前用户 | 获取当前用户信息 `GET /auth/me` | P0 |

## 2. 业务流程

```
微信用户 -> 登录(/auth/login) -> 获取当前用户(/auth/me)
  -> 创建咨询会话 -> QA问答(同步/流式) -> AI诊断完成
  -> 转工单(submit) -> 确认派单(ack)
  |-> 催办(cuiban-notification)
  |-> 升级(assignable-users -> 建单)
  |-> 讨论(comments)
  |-> 查看我的工单
```

## 3. 状态流转

引用系统任务模块状态机（`automation/docs/testing/analysis/analysis-tasks.md`）：

```
pending -> in_progress -> resolved -> closed
pending -> cancelled
in_progress -> cancelled
```

转工单中的 ticket 状态：
```
created -> acknowledged -> resolved
```

## 4. 权限控制

| 接口 | 权限要求 |
|------|---------|
| POST /auth/login | 无需认证 |
| GET /auth/me | 需有效 JWT Token |
| POST /api/conversations | 需 JWT Token |
| GET /api/conversations | 需 JWT Token，只能看自己的 |
| GET /api/conversations/{id} | 需 JWT Token，只能看自己的 |
| POST /api/qa/ask | 需 JWT Token |
| POST /api/qa/ask/stream | 需 JWT Token |
| POST /api/qa/submit | 需 JWT Token |
| POST /api/ai/qa/ticket/ack | 需 JWT Token，仅工单所属人可操作 |
| POST /api/tasks/cuiban-notification | 需 JWT Token |
| GET /api/tasks/assignable-users | 需 JWT Token |
| POST /api/messages | 需 JWT Token |
| GET /api/messages | 需 JWT Token |
| GET /api/my-tasks | 需 JWT Token，只显示自己的 |
| POST /api/my-tasks | 需 JWT Token |

## 5. 接口列表

| 方法 | 路径 | 说明 | 版本 | Mock 已有? |
|------|------|------|------|-----------|
| POST | /auth/login | 登录 | v1 | ✅ |
| GET | /auth/me | 当前用户 | v1 | ✅ |
| POST | /api/conversations | 创建会话 | v1 | ✅ |
| GET | /api/conversations | 会话列表 | v1 | ✅ |
| GET | /api/conversations/{id} | 会话详情 | v1 | ✅ |
| POST | /api/qa/ask | QA同步问答 | v1 | ✅ |
| POST | /api/qa/ask/stream | QA流式问答 | v1 | ✅ |
| POST | /api/ai/qa/submit | 转工单 | v1 | ❌ 需新增 |
| POST | /api/ai/qa/ticket/ack | 确认派单 | v1 | ❌ 需新增 |
| POST | /api/tasks/cuiban-notification | 催办 | v1 | ❌ 需新增 |
| GET | /api/tasks/assignable-users | 升级选人 | v1 | ❌ 需新增 |
| POST | /api/messages | 发送消息 | v1 | ✅ |
| GET | /api/messages | 消息列表 | v1 | ✅ |
| GET | /api/my-tasks | 我的工单列表 | v1 | ✅ |
| POST | /api/my-tasks | 创建我的工单 | v1 | ✅ |
| POST | /api/tasks/{id}/comments | 添加评论 | v1 | ✅ |
| GET | /api/tasks/{id}/comments | 评论列表 | v1 | ✅ |

## 6. 风险点

| 风险 | 等级 | 影响 | 建议处理 |
|------|------|------|---------|
| 转工单提交失败 | P0 | 用户看到 AI 方案但无法转工单 | 添加重试+回退人工方案 |
| 确认派单后状态不一致 | P0 | ticket ack 但工单未创建 | 事务保证 |
| 催办频繁 | P1 | 工程师被骚扰 | Redis 限流（1 次/5min） |
| 升级选人列表为空 | P1 | 无法升级 | 降级到上级自动分配 |
| SSE 流式连接异常 | P1 | 客户端看不到完整回答 | 添加重试和超时机制 |
| 空文本 QA 请求 | P1 | 服务器报错 | 前端校验+后端422 |
| 会话 ID 不存在 | P1 | 用户看到错误信息 | 统一404响应 |
| 大序列消息输入 | P2 | 性能下降或内存溢出 | 字数限制 |

## 7. 边界条件

| 条件 | 说明 | 预期行为 |
|------|------|---------|
| 转工单内容为空 | 不传 body | 422 |
| 确认派单 ticket_id=-1 | 非法 ID | 404 |
| 确认派单重复 ack | 已确认的 ticket | 400 |
| 催办 task_id=-1 | 非法 ID | 404 |
| 催办备注超长 | 10K+ 字符 | 422 或截断 |
| 催办已 closed 工单 | 终态工单 | 400 |
| 升级选人 project_id=0 | 非法 ID | 400 |
| 升级选人无可用人员 | 返回空数组 | 200 [] |
| 登录密码错误 | 错误密码 | 401 |
| 登录空密码 | password="" | 422 |
| token 过期 | 过期 JWT | 401 |
| 会话标题为空 | 不传标题 | 200，使用默认标题 |
| QA 问题为空 | 参数缺失或为空 | 422 |
| 消息内容超长 | 10K+ 字符 | 422 或截断 |
| 并发创建会话 | 同时发起多个请求 | 成功，ID 自增 |
