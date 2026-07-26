# 测试分析报告：系统任务

> 基于 [PRD.md](../PRD.md)
---

系统任务」（供给视角）

> PRD 第七节 · Owner：后端 + 任务 Agent 工程师

### 2.1 功能点

| 编号 | 功能点 | 用户故事 |
|------|--------|---------|
| F10 | 统一任务收件箱 | 查看分配给自己的所有工单 |
| F11 | 任务详情处理 | 查看完整信息 + AI 分析并提交解决方案 |
| F12 | 任务状态流转 | 更新工单状态（接单/处理/解决） |
| F13 | 任务转派 | 将工单转派给其他工程师 |
| F14 | 任务上报 | 上报无法处理的工单给上级 |
| F15 | AI 辅助分析 | AI 分析工单根因并给出处理建议 |
| F16 | AI 自动派单 | 系统自动将新工单分派给最合适的工程师 |
| F17 | 任务讨论 | 在工单下与相关人员讨论 |
| F18 | 工单统计 | 查看团队工单处理效率 |

### 2.2 业务流程

AI 自动派单（Assigner）→ 任务进入工程师收件箱
→ 工程师查看任务详情（AI 分析摘要）
→ 工程师接单（pending→in_progress）
→ AI 辅助分析 / 提交解决方案 / 转派给其他工程师 / 上报给上级
→ 客户确认关闭 / 管理员关闭
→ 工单关闭 → AI 总结 → 人工审核 → 入知识库

### 2.3 状态流转

任务（Task）状态机：

new → ai_assign/manual_assign → assigned → in_progress → resolved → closed
assigned → cancelled / timeout
in_progress → forwarded / escalated
forwarded → in_progress（被转派人接单）
resolved → reopened

非法流转：new→in_progress / assigned→resolved / cancelled→in_progress

### 2.4 权限控制

| 角色 | 能力 |
|------|------|
| 工程师 | 收件箱、处理、转派、上报、讨论 |
| 管理员 | 全部（含手动派单、统计） |
| 客户方 | 仅讨论（公开评论） |

### 2.5 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/tasks | GET/POST | 任务列表/创建 |
| /api/tasks/{id} | GET/PUT/DELETE | 任务详情/更新/删除 |
| /api/tasks/{id}/status | PATCH | 状态流转 |
| /api/tasks/{id}/assign | PATCH | 分派 |
| /api/tasks/{id}/ai-assign | POST | AI 派单 |
| /api/tasks/filter | POST | 过滤查询 |
| /api/tasks/stats/overview | GET | 统计概览 |
| /api/tasks/{id}/comments | GET/POST | 评论 CRUD |
| /api/tasks/assignable-users | GET | 可选用户列表 |
| /api/ai/task/analyze/stream | POST | AI 分析流式 |
| /api/ai/task/submit | POST | 提交解决方案 |

### 2.6 风险点

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| AI 派单分配不合理 | P1 | Assigner 四层流水线 + 规则回退兜底 |
| 状态流转并发冲突 | P1 | 乐观锁 / 事务锁 |
| 转派后原工程师仍被通知 | P2 | 转派后更新通知接收人 |
| 解决方案提交失败 | P1 | 前端草稿箱 + 重试 |

### 2.7 边界条件

| 场景 | 预期行为 |
|------|---------|
| 工单已分配给 A，B 也查看 | B 只能看到，不能操作 |
| 工程师试图接单已被接的单 | 返回 409 Conflict |
| 试图转派不存在的工程师 | 返回 404 |
| 状态流转触发非法跳转 | 返回 400 + 合法路径提示 |
| AI 分析超时 >30s | 降级为 AI 暂时无法分析 + 人工处理 |
| 收件箱为空 | 显示空状态 + 引导文案 |

---
# 测试分析报告：系统任务

> 基于 [PRD.md](../PRD.md)

---

系统任务」（供给视角）

> PRD 第七节 · Owner：后端 + 任务 Agent 工程师

### 2.1 功能点

| 编号 | 功能点 | 用户故事 |
|------|--------|---------|
| F10 | 统一任务收件箱 | 查看分配给自己的所有工单 |
| F11 | 任务详情处理 | 查看完整信息 + AI 分析并提交解决方案 |
| F12 | 任务状态流转 | 更新工单状态（接单/处理/解决） |
| F13 | 任务转派 | 将工单转派给其他工程师 |
| F14 | 任务上报 | 上报无法处理的工单给上级 |
| F15 | AI 辅助分析 | AI 分析工单根因并给出处理建议 |
| F16 | AI 自动派单 | 系统自动将新工单分派给最合适的工程师 |
| F17 | 任务讨论 | 在工单下与相关人员讨论 |
| F18 | 工单统计 | 查看团队工单处理效率 |

### 2.2 业务流程

AI 自动派单（Assigner）→ 任务进入工程师收件箱
→ 工程师查看任务详情（AI 分析摘要）
→ 工程师接单（pending→in_progress）
→ AI 辅助分析 / 提交解决方案 / 转派给其他工程师 / 上报给上级
→ 客户确认关闭 / 管理员关闭
→ 工单关闭 → AI 总结 → 人工审核 → 入知识库

### 2.3 状态流转

任务（Task）状态机：

new → ai_assign/manual_assign → assigned → in_progress → resolved → closed
assigned → cancelled / timeout
in_progress → forwarded / escalated
forwarded → in_progress（被转派人接单）
resolved → reopened

非法流转：new→in_progress / assigned→resolved / cancelled→in_progress

### 2.4 权限控制

| 角色 | 能力 |
|------|------|
| 工程师 | 收件箱、处理、转派、上报、讨论 |
| 管理员 | 全部（含手动派单、统计） |
| 客户方 | 仅讨论（公开评论） |

### 2.5 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/tasks | GET/POST | 任务列表/创建 |
| /api/tasks/{id} | GET/PUT/DELETE | 任务详情/更新/删除 |
| /api/tasks/{id}/status | PATCH | 状态流转 |
| /api/tasks/{id}/assign | PATCH | 分派 |
| /api/tasks/{id}/ai-assign | POST | AI 派单 |
| /api/tasks/filter | POST | 过滤查询 |
| /api/tasks/stats/overview | GET | 统计概览 |
| /api/tasks/{id}/comments | GET/POST | 评论 CRUD |
| /api/tasks/assignable-users | GET | 可选用户列表 |
| /api/ai/task/analyze/stream | POST | AI 分析流式 |
| /api/ai/task/submit | POST | 提交解决方案 |

### 2.6 风险点

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| AI 派单分配不合理 | P1 | Assigner 四层流水线 + 规则回退兜底 |
| 状态流转并发冲突 | P1 | 乐观锁 / 事务锁 |
| 转派后原工程师仍被通知 | P2 | 转派后更新通知接收人 |
| 解决方案提交失败 | P1 | 前端草稿箱 + 重试 |

### 2.7 边界条件

| 场景 | 预期行为 |
|------|---------|
| 工单已分配给 A，B 也查看 | B 只能看到，不能操作 |
| 工程师试图接单已被接的单 | 返回 409 Conflict |
| 试图转派不存在的工程师 | 返回 404 |
| 状态流转触发非法跳转 | 返回 400 + 合法路径提示 |
| AI 分析超时 >30s | 降级为 AI 暂时无法分析 + 人工处理 |
| 收件箱为空 | 显示空状态 + 引导文案 |

---
