# 系统任务模块 - 测试场景设计

## 1. 正常流程

| 编号 | 标题 | 用例 |
|------|------|------|
| T-F1 | AI 方案分析 | POST /api/ai/task/analyze → 200 |
| T-F2 | AI 方案分析流式 | POST /api/ai/task/analyze/stream → SSE |
| T-F3 | AI 方案提交 | POST /api/ai/task/submit → 200 |
| T-F4 | AI 任务列表 | POST /api/ai/task/list → 200 |
| T-F5 | AI 聊天 | POST /api/ai/task/chat → 200 |
| T-F6 | AI 聊天流式 | POST /api/ai/task/chat/stream → SSE |
| T-F7 | AI 健康检查 | GET /api/ai/task/health → 200 |
| T-F8 | 接单操作 | PATCH /api/tasks/{id}/assign {myself} → 200 |
| T-F9 | 转派操作 | PATCH /api/tasks/{id}/assign {other} → 200 |
| T-F10 | 多任务类型创建 | POST /api/tasks {type=bug/requirement/support} → 200 |

## 2. 异常流程

| 编号 | 标题 | 用例 |
|------|------|------|
| T-E1 | AI 方案分析 ID 不存在 | POST /api/ai/task/analyze {task_id=-1} → 404 |
| T-E2 | AI 方案提交空方案 | POST /api/ai/task/submit {solution=""} → 400 |
| T-E3 | AI 任务列表空 | POST /api/ai/task/list {空条件} → 200 [] |
| T-E4 | 接单已分配工单 | PATCH /api/tasks/{id=已分配}/assign → 400 |

## 3. 权限

| 编号 | 标题 | 用例 |
|------|------|------|
| T-A1 | AI 分析无 token | POST /api/ai/task/analyze 无 auth → 401 |
| T-A2 | 接单他人工单(非 assigned) | 普通用户 PATCH assign → 403 |
| T-A3 | 转派无 admin 权限 | 非 admin 转派 → 403 |

## 4. 状态流转

| 编号 | 标题 | 用例 |
|------|------|------|
| T-S1 | 已 closed 不可重开 | closed → in_progress → 400 |
| T-S2 | cancelled 不可接单 | cancelled → PATCH assign → 400 |
| T-S3 | pending → in_progress(接单) | 正常流转 |
| T-S4 | in_progress 上报 → 待讨论 | 状态机扩展场景 |

## 5. 数据校验

| 编号 | 标题 | 用例 |
|------|------|------|
| T-D1 | AI 分析参数缺 task_id | POST /api/ai/task/analyze 无 body → 422 |
| T-D2 | AI 聊天消息超长 | POST /api/ai/task/chat {message: 10K+} → 422 |
| T-D3 | 任务类型非法 | POST /api/tasks {type=invalid} → 400 |
| T-D4 | 分页 size 超限 | GET /api/tasks?size=200 → 422 |

## 6. Redis 缓存

| 编号 | 标题 | 说明 |
|------|------|------|
| T-R1 | 任务列表缓存 | 相同查询条件缓存命中 → 快响应 |
| T-R2 | AI 分析缓存 | 相同 task_id 分析结果缓存 |

## 7. AI

| 编号 | 标题 | 说明 |
|------|------|------|
| T-AI1 | 流式连接中断 | SSE 中断后重连 → 续传 |
| T-AI2 | AI 返回不可解析格式 | analyze 返回非法 JSON → 降级 |
| T-AI3 | AI 返回空方案 | submit 前 analyze 无推荐 → 人工 |

## 8. 数据库

| 编号 | 标题 | 说明 |
|------|------|------|
| T-DB1 | 并发状态更新 | 两人同时操作状态 → 乐观锁冲突 409 |
| T-DB2 | AI 方案提交事务 | 写入方案失败 → 回滚 |

---

## 汇总：标准用例清单

| 用例ID | 功能 | 标题 | 覆盖类型 | 优先级 |
|--------|------|------|---------|--------|
| TASK-001 | AI 方案分析 | 正常-分析成功 | 正常流程 | P1 |
| TASK-002 | AI 方案分析 | 异常-task_id不存在 | 异常流程 | P1 |
| TASK-003 | AI 方案分析 | 权限-无token | 权限 | P1 |
| TASK-004 | AI 方案分析 | 数据校验-缺参数 | 数据校验 | P1 |
| TASK-005 | AI 方案分析流式 | 正常-SSE返回 | 正常流程 | P1 |
| TASK-006 | AI 方案流式 | AI-流式中断重连 | AI | P1 |
| TASK-007 | AI 方案提交 | 正常-提交成功 | 正常流程 | P1 |
| TASK-008 | AI 方案提交 | 异常-空方案 | 异常流程 | P1 |
| TASK-009 | AI 方案提交 | DB-事务回滚 | 数据库 | P1 |
| TASK-010 | AI 聊天 | 正常-聊天返回 | 正常流程 | P1 |
| TASK-011 | AI 聊天 | 异常-消息超长 | 数据校验 | P1 |
| TASK-012 | AI 聊天流式 | 正常-SSE流式 | 正常流程 | P1 |
| TASK-013 | AI 任务列表 | 正常-返回列表 | 正常流程 | P1 |
| TASK-014 | AI 任务列表 | 异常-空列表 | 异常流程 | P1 |
| TASK-015 | AI 健康检查 | 正常-健康返回 | 正常流程 | P1 |
| TASK-016 | 接单 | 正常-工程师接单 | 正常流程 | P0 |
| TASK-017 | 接单 | 异常-已分配不可重复 | 异常流程 | P0 |
| TASK-018 | 接单 | 状态-cancelled不可接 | 状态流转 | P0 |
| TASK-019 | 转派 | 正常-admin转派到他人 | 正常流程 | P0 |
| TASK-020 | 转派 | 权限-非admin不可转派 | 权限 | P0 |
| TASK-021 | 多任务类型 | 正常-type=bug创建 | 正常流程 | P1 |
| TASK-022 | 多任务类型 | 正常-type=requirement创建 | 正常流程 | P1 |
| TASK-023 | 多任务类型 | 正常-type=support创建 | 正常流程 | P1 |
| TASK-024 | 多任务类型 | 数据校验-type非法 | 数据校验 | P1 |
| TASK-025 | AI 分析 | Redis-结果缓存 | Redis | P1 |
| TASK-026 | 状态流转 | 终态不可重开(closed) | 状态流转 | P0 |
| TASK-027 | 状态流转 | 终态不可接单(cancelled) | 状态流转 | P0 |
| TASK-028 | 任务列表 | 数据校验-size超限 | 数据校验 | P1 |
| TASK-029 | 并发 | DB-乐观锁冲突 | 数据库 | P1 |
