# 测试分析报告：我要摇人

> 基于 [PRD.md](../PRD.md)
---

我要摇人」（需求视角）

> PRD 第六节 · Owner：前端 + 知识库/QA Agent

### 1.1 功能点

| 编号 | 功能点 | 用户故事 |
|------|--------|---------|
| F1 | 一键报障提单 | 快速提交故障/问题 |
| F2 | AI 在线咨询诊断 | AI 引导描述问题 |
| F3 | 工单列表查看 | 查看我提交的工单 |
| F4 | 工单详情查看 | 查看单个工单完整信息 |
| F5 | 工单催办 | 催办处理中的工单 |
| F6 | 工单上报 | 上报给上级处理 |
| F7 | 工单升级 | 升级为高优先级工单 |
| F8 | 工单讨论 | 留言讨论 |
| F9 | AI 诊断摘要 | 查看 AI 诊断结论 |

### 1.2 业务流程

用户进入微信服务号 → 一键报障/在线咨询 → AI 引导诊断（SSE流式）
→ 用户确认解决? → 结束 / 转工单
→ 工单预览（AI生成摘要）→ 提交工单
→ 我的工单列表（催办/上报/升级/讨论）

### 1.3 状态流转

工单（Ticket）状态机：

pending → in_progress → resolved → closed
pending → cancelled
resolved → reopened → in_progress

非法流转：pending→resolved / in_progress→closed / cancelled→in_progress / closed→in_progress

### 1.4 权限控制

| 角色 | 能力 |
|------|------|
| 客户方用户 | 提交工单、查看自己的工单、催办/上报/升级/讨论、关闭自己的工单 |
| 工程师 | 查看被分配的工单、讨论、确认派单 |
| 管理员 | 全部（含查看所有工单、处理、分派、关闭） |

### 1.5 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/ai/qa/submit | POST | 转工单 |
| /api/call/conversations | GET/POST | 会话 CRUD |
| /api/call/qa/ask | POST | 同步 QA |
| /api/call/qa/ask/stream | POST | 流式 QA（SSE） |
| /api/call/messages | GET/POST | 消息 CRUD |
| /api/my-tasks/ | GET/POST | 我的工单列表/创建 |
| /api/tasks/{id}/comments | GET/POST | 工单评论 |
| /api/tasks/cuiban-notification | POST | 催办/上报通知 |
| /api/tasks/assignable-users | GET | 可分配用户列表 |
| /api/auth/dev-login | POST | 微信降级登录 |

### 1.6 风险点

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| SSE 首字节延迟 >3s | P0 | 性能基线 + 超时告警 |
| AI 诊断无命中知识库 | P1 | 降级为通用引导 + 转人工 |
| 附件上传超时/失败 | P1 | 分片上传 + 重试 |
| 语音输入部分浏览器不可用 | P1 | 检测 API + 降级为文本 |
| 催办/上报通知送达失败 | P2 | 降级为日志 + Toast |
| 对话历史丢失 | P1 | session_id 持久化 + localStorage 兜底 |

### 1.7 边界条件

| 场景 | 预期行为 |
|------|---------|
| 未登录访问工单页 | 跳转微信 OAuth 登录 |
| AI 回复中用户关闭页面 | 下次进入自动恢复会话 |
| 附件超过大小限制 | 前端拦截 + 提示 |
| 催办时缺 deadline_at/assigned_to | 后端返回 400 并透传 |
| 升级未选择对象直接提交 | 前端拦截提示 |
| 工单列表 >100 条 | 普通流渲染 + 容器滚动 |

---
# 测试分析报告：我要摇人

> 基于 [PRD.md](../PRD.md)

---

我要摇人」（需求视角）

> PRD 第六节 · Owner：前端 + 知识库/QA Agent

### 1.1 功能点

| 编号 | 功能点 | 用户故事 |
|------|--------|---------|
| F1 | 一键报障提单 | 快速提交故障/问题 |
| F2 | AI 在线咨询诊断 | AI 引导描述问题 |
| F3 | 工单列表查看 | 查看我提交的工单 |
| F4 | 工单详情查看 | 查看单个工单完整信息 |
| F5 | 工单催办 | 催办处理中的工单 |
| F6 | 工单上报 | 上报给上级处理 |
| F7 | 工单升级 | 升级为高优先级工单 |
| F8 | 工单讨论 | 留言讨论 |
| F9 | AI 诊断摘要 | 查看 AI 诊断结论 |

### 1.2 业务流程

用户进入微信服务号 → 一键报障/在线咨询 → AI 引导诊断（SSE流式）
→ 用户确认解决? → 结束 / 转工单
→ 工单预览（AI生成摘要）→ 提交工单
→ 我的工单列表（催办/上报/升级/讨论）

### 1.3 状态流转

工单（Ticket）状态机：

pending → in_progress → resolved → closed
pending → cancelled
resolved → reopened → in_progress

非法流转：pending→resolved / in_progress→closed / cancelled→in_progress / closed→in_progress

### 1.4 权限控制

| 角色 | 能力 |
|------|------|
| 客户方用户 | 提交工单、查看自己的工单、催办/上报/升级/讨论、关闭自己的工单 |
| 工程师 | 查看被分配的工单、讨论、确认派单 |
| 管理员 | 全部（含查看所有工单、处理、分派、关闭） |

### 1.5 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/ai/qa/submit | POST | 转工单 |
| /api/call/conversations | GET/POST | 会话 CRUD |
| /api/call/qa/ask | POST | 同步 QA |
| /api/call/qa/ask/stream | POST | 流式 QA（SSE） |
| /api/call/messages | GET/POST | 消息 CRUD |
| /api/my-tasks/ | GET/POST | 我的工单列表/创建 |
| /api/tasks/{id}/comments | GET/POST | 工单评论 |
| /api/tasks/cuiban-notification | POST | 催办/上报通知 |
| /api/tasks/assignable-users | GET | 可分配用户列表 |
| /api/auth/dev-login | POST | 微信降级登录 |

### 1.6 风险点

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| SSE 首字节延迟 >3s | P0 | 性能基线 + 超时告警 |
| AI 诊断无命中知识库 | P1 | 降级为通用引导 + 转人工 |
| 附件上传超时/失败 | P1 | 分片上传 + 重试 |
| 语音输入部分浏览器不可用 | P1 | 检测 API + 降级为文本 |
| 催办/上报通知送达失败 | P2 | 降级为日志 + Toast |
| 对话历史丢失 | P1 | session_id 持久化 + localStorage 兜底 |

### 1.7 边界条件

| 场景 | 预期行为 |
|------|---------|
| 未登录访问工单页 | 跳转微信 OAuth 登录 |
| AI 回复中用户关闭页面 | 下次进入自动恢复会话 |
| 附件超过大小限制 | 前端拦截 + 提示 |
| 催办时缺 deadline_at/assigned_to | 后端返回 400 并透传 |
| 升级未选择对象直接提交 | 前端拦截提示 |
| 工单列表 >100 条 | 普通流渲染 + 容器滚动 |

---
