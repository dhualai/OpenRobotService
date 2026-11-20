# 系统任务模块 - 业务分析

## 1. 功能点

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 工单创建 | 提交故障工单（必填标题+描述，支持 type 字段） | P0 |
| 工单列表 | 获取工单列表（支持分页） | P0 |
| 工单详情 | 查看单个工单详细信息 | P0 |
| 工单更新 | 修改工单字段（标题/描述/优先级等） | P0 |
| 状态转换 | 按状态机转换工单状态 | P0 |
| 分配处理人 | 将工单分配给工程师 | P0 |
| 接单 | 工程师领取工单（PATCH assign 给自己） | P0 |
| 转派 | admin 将工单转给他人 | P0 |
| 工单删除 | 删除工单 | P1 |
| 关键词筛选 | 按关键词筛选工单 | P1 |
| 工单统计 | 获取工单统计数据 | P1 |
| 评论管理 | 添加/查看工单评论 | P1 |
| AI 自动分派 | AI 根据业务内容自动分配处理人 | P2 |
| AI 方案分析 | AI 生成解决方案 `POST /api/ai/task/analyze` | P1 |
| AI 方案分析流式 | AI 方案 SSE 流式 `POST /api/ai/task/analyze/stream` | P1 |
| AI 方案提交 | 提交 AI 方案 `POST /api/ai/task/submit` | P1 |
| AI 聊天 | AI 工单讨论 `POST /api/ai/task/chat` | P1 |
| AI 聊天流式 | AI 讨论 SSE 流式 `POST /api/ai/task/chat/stream` | P1 |
| AI 任务列表 | AI 相关任务列表 `POST /api/ai/task/list` | P1 |
| AI 健康检查 | AI 服务健康 `GET /api/ai/task/health` | P1 |
| 多任务类型 | 支持 bug/requirement/support 三类 | P1 |

## 2. 业务流程

```
客户提交故障 -> 创建工单(pending, type=bug/support/requirement)
  -> 系统自动分配 -> 工程师接单(in_progress)
  -> AI 方案分析(analyze) -> 提交方案(submit)
  -> 工程师诊断+评论 -> 解决(resolved)
  |-> 升级(upgrade -> waiting)
  |-> 讨论(chat)
  -> 客户确认关闭(closed)
  或 pending -> cancelled（取消）
  或 in_progress -> cancelled（取消）
```

## 3. 状态流转

```
pending -> in_progress -> resolved -> closed
pending -> cancelled
in_progress -> cancelled
in_progress -> waiting(待讨论/已上报) -> in_progress
```

| 源状态 | 目标状态 | 合法性 | 说明 |
|--------|---------|--------|------|
| pending | in_progress | ✅ | 接单 |
| pending | cancelled | ✅ | 取消 |
| in_progress | resolved | ✅ | 已解决 |
| in_progress | cancelled | ✅ | 取消 |
| in_progress | waiting | ✅ | 等待讨论/上报 |
| waiting | in_progress | ✅ | 重新处理 |
| resolved | closed | ✅ | 关闭 |
| closed | 任意 | ❌ | 终态 |
| cancelled | 任意 | ❌ | 终态 |
| pending | closed | ❌ | 跳过中间状态 |
| pending | resolved | ❌ | 跳过中间状态 |

## 4. 权限控制

| 角色 | 可操作 |
|------|-------|
| admin | 全部权限（创建/分配/接单/转派/删除/查看全部/AI 全部） |
| engineer | 创建工单、接单、处理、添加评论、AI 分析/聊天 |
| customer | 只能查看自己创建的工单，可评论和关闭 |

## 5. 接口列表

| 方法 | 路径 | 说明 | 版本 | Mock 已有? |
|------|------|------|------|-----------|
| POST | /api/tasks | 创建工单 | v1 | ✅ |
| GET | /api/tasks | 工单列表 | v1 | ✅ |
| GET | /api/tasks/{id} | 工单详情 | v1 | ✅ |
| PUT | /api/tasks/{id} | 更新工单 | v1 | ✅ |
| PATCH | /api/tasks/{id}/status | 状态转换 | v1 | ✅ |
| PATCH | /api/tasks/{id}/assign | 分配/接单/转派 | v1 | ⚠️ 需扩展 |
| DELETE | /api/tasks/{id} | 删除工单 | v1 | ✅ |
| POST | /api/tasks/filter | 筛选工单 | v1 | ✅ |
| GET | /api/tasks/stats/overview | 工单统计 | v1 | ✅ |
| POST | /api/tasks/{id}/comments | 添加评论 | v1 | ✅ |
| GET | /api/tasks/{id}/comments | 评论列表 | v1 | ✅ |
| POST | /api/tasks/{id}/ai-assign | AI 自动分派 | v1 | ✅ |
| POST | /api/ai/task/analyze | AI 方案分析 | v1 | ✅ |
| POST | /api/ai/task/analyze/stream | AI 方案分析流式 | v1 | ✅ |
| POST | /api/ai/task/submit | AI 方案提交 | v1 | ❌ 需新增 |
| POST | /api/ai/task/chat | AI 聊天 | v1 | ❌ 需新增 |
| POST | /api/ai/task/chat/stream | AI 聊天流式 | v1 | ❌ 需新增 |
| POST | /api/ai/task/list | AI 任务列表 | v1 | ❌ 需新增 |
| GET | /api/ai/task/health | AI 健康检查 | v1 | ❌ 需新增 |

## 6. 风险点

| 风险 | 等级 | 影响 | 建议处理 |
|------|------|------|---------|
| 非法状态转换 | P0 | 状态机被绕过 | 后端校验返回400 |
| 并发状态更新 | P0 | 重复处理或状态丢失 | 乐观锁或版本号 |
| AI 方案分析 SSE 断连 | P1 | 客户端看不到完整分析 | 确保重连机制 |
| AI 提交空方案 | P1 | 无意义方案 | 后端校验，降级到人工 |
| AI 服务不可用 | P1 | 无法分析 | 降级到规则匹配 |
| 接单已分配工单 | P1 | 重复领取 | 400 |
| 转派权限绕开 | P1 | 非 admin 转派 | 严格权限校验 |
| 评论超长 | P1 | 性能下降 | 字数限制 |
| 删除已分配工单 | P2 | 数据丢失 | 确认提示或软删除 |

## 7. 边界条件

| 条件 | 说明 | 预期行为 |
|------|------|---------|
| 工单标题为空 | 必填字段缺失 | 422 |
| 工单 type 非法 | 传递不支持的类型 | 400 |
| 工单 ID 不存在 | 获取/更新/删除不存在的工单 | 404 |
| 状态值为空 | 传递空状态 | 400 |
| 状态值无效 | 传递不存在的状态值 | 400 |
| pending 直接转 closed | 跳过中间状态 | 400 |
| closed 转 in_progress | 终态不可回退 | 400 |
| cancelled 转 in_progress | 终态不可回退 | 400 |
| 接单已分配工单 | 重复 assign | 400 |
| 非 admin 转派 | 权限不足 | 403 |
| AI 分析 task_id 不存在 | 非法 ID | 404 |
| AI 分析缺参数 | 无 body | 422 |
| AI 聊天消息超长 | 10K+ 字符 | 422 或截断 |
| 评论内容超长 | 10K+ 字符 | 422 或截断 |
| 分页 size 超限 | size>100 | 422 或截断到 100 |
| 关键词筛选为空 | 不传参数 | 返回全部工单 |
