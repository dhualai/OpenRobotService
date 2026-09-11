# 业务链路规范候选：摇人问答 -> 提单 -> 派单 -> 处理 -> 关闭

> 状态：S1-S24 已人工 review 通过；第一版采用 `problem` 类型，完整走 3 个阶段
> 日期：2026-09-09
> 测试目标：真实后端环境
> 说明：本文件只描述业务链路，不包含测试代码
> 已确认：已解决后由提单用户 U1 在真实前端点击“确认关闭”
> 已确认：派单采用后端“指定处理人”强信号机制，U1 在固定问答中明确提单给显示名“自动化处理人”

## 1. 链路一句话

提单用户进入“我要摇人”，通过固定问答让 AI 完成问题描述和信息收集，点击转工单后确认提单；系统生成 AI 工单并自动派给处理人；处理人接单、按协商阶段推进、填写解决方式并提交已解决；提单用户确认关闭工单。

## 2. 角色

| 角色 | 代号 | 说明 |
|------|------|------|
| 提单用户 | U1 | 负责提问、补充信息、确认提单，并在已解决后确认关闭 |
| 处理人 | U2 | 负责接收工单、确认协商节点、处理并提交已解决 |
| AI 问答服务 | AI | 固定问题 + 预设回复，不验证 AI 质量 |
| 自动派单 | Worker | 扫描 `source=ai` 且 `status=new` 的工单；命中“指定处理人”直接指派 U2 |

## 3. 前置条件

1. U1、U2 已在测试环境创建并完成登录验证（`u1_auto` / `自动化提单用户`，`u2_auto` / `自动化处理人`）。
2. U1 拥有可提单的项目，项目编号和项目名称在环境中存在。
3. U2 是系统 active 用户，显示姓名唯一；不需要为 U2 额外配置派单画像。
4. AI 问答可配置为固定问题 + 预设回复，不影响真实用户。
5. 固定问答脚本中，U1 会明确表达：
   `指定处理人：自动化处理人`（不能只写登录名 `u2_auto`，强信号匹配使用显示名）。
6. 第一版提单工单类型采用 `problem`，环境中已有 3 阶段模板：初步诊断 / 临时解决 / 最终解决。
7. AI 派单 Worker 与 Redis 派单事件通道可用。

## 4. 前端页面入口

| 页面 | 路由 | 前端文件 |
|------|------|---------|
| 登录页 | `/login` | `frontend/src/pages/Login.tsx` |
| 我要摇人 | `/call` | `frontend/src/pages/call/CallView.tsx` |
| 系统任务列表 | `/tasks` | `frontend/src/pages/tasks/TasksView.tsx` |
| 系统任务详情 | `/tasks/{id}` | `frontend/src/pages/tasks/TaskDetailPage.tsx` |
| 摇人历史工单 | `/call/history` | `frontend/src/pages/call/HistoryTicketsPage.tsx` |

## 5. 主链路步骤

### 阶段一：U1 登录并进入“我要摇人”

| 步骤 | 前端操作 | 角色 | 接口 / 事件 | 主要入参 | 预期结果 |
|------|---------|------|-------------|---------|---------|
| S1 | 在登录页输入 U1 账号密码并登录 | U1 | `POST /api/auth/login` | `username` / `password` | 200，返回 `access_token`；前端进入 `/call` |

### 阶段二：U1 与 AI 固定问答

| 步骤 | 前端操作 | 角色 | 接口 / 事件 | 主要入参 | 预期结果 |
|------|---------|------|-------------|---------|---------|
| S2 | 新建会话并发送第一条固定问题 | U1 | `POST /api/call/conversations` | `scene_type=chat`，`metadata_.ai_session_id=新sid` | 200，返回会话 id；会话建立 |
| S3 | 用户问题写入会话 | U1 | `POST /api/call/messages` | `conversation_id`、`role=user`、问题文本 | 200，返回 DB 消息 id |
| S4 | AI 开始流式回答 | AI | `POST /api/ai/qa/ask/stream`（SSE） | `session_id`、`query`、`conversation_id` | 收到 `token` / `status` / `result` 等 SSE 事件 |
| S5 | AI 回复写入会话 | AI | 后端经 SSE 事件或前端兜底 `POST /api/call/messages` | `role=assistant`、回复文本 | 会话中能看到 U1 问题和 AI 回复 |
| S6 | 若 AI 继续追问必填信息，U1 按预设脚本逐条补充 | U1 + AI | 重复 S3-S5 | 每次一条固定补充内容 | AI 最终判断信息完整，可进入提单 |

已确认第一版固定问答：

```text
U1：
我要在项目 Leo_test（摇人吧服务号-测试）提一个 problem 工单。
指定处理人：自动化处理人。
标题：自动化链路验证-{run_id}。
问题：机器人无法启动，故障码 E1001，已尝试重启仍无效。
请直接生成工单草稿，不要继续追问。

AI：
工单草稿已生成。
类型：problem
项目：Leo_test（摇人吧服务号-测试）
指定处理人：自动化处理人
标题：自动化链路验证-{run_id}
可以直接转工单。
```

测试不直接断言 AI 的中文话术全文，以下结构化结果才是稳定契约：

1. `prepare.stage = draft_ready`。
2. `prepare.ticket_ready = true`。
3. `draft.type = problem`。
4. `draft.project_id = Leo_test`。
5. `draft.title` 包含本次运行的唯一标记。
6. `draft.description` 包含 `[指定处理人：自动化处理人]`。

若后续需要多轮追问，也必须在可控回复模式下保持固定轮次和固定字段结果。

### 阶段三：U1 转工单并确认提单

| 步骤 | 前端操作 | 角色 | 接口 / 事件 | 主要入参 | 预期结果 |
|------|---------|------|-------------|---------|---------|
| S7 | U1 点击“转工单” | U1 | `POST /api/ai/qa/ticket/prepare` | `session_id` | 返回草稿；`stage=draft_ready` 或 `need_fields` |
| S8 | 若返回 `need_fields`，U1 回对话补充，再点转工单 | U1 | 重复 S6-S7 | 补充内容 | `stage=draft_ready`，弹窗展示草稿；描述包含 `[指定处理人：自动化处理人]` |
| S9 | 弹窗打开时拉取处理阶段 | U1 | `GET /api/ai/qa/ticket/steps` | `type=工单类型` | 返回协商阶段列表；U1 选择处理阶段和阶段时间 |
| S10 | U1 核对并确认提单 | U1 | `POST /api/ai/qa/ticket/confirm` | `session_id`、`overrides`、`username` | `code=0`；返回 `data.db_id` 和入库工单；业务状态为新建 |
| S11 | 工单概览气泡写入会话 | U1 | `POST /api/call/messages` | `metadata_=ticket_overview` | 对话区出现工单概览气泡 |

### 阶段四：等待自动派单

| 步骤 | 前端操作 | 角色 | 接口 / 事件 | 主要入参 | 预期结果 |
|------|---------|------|-------------|---------|---------|
| S12 | 前端轮询工单概览状态 | U1 | `GET /api/tasks/{db_id}` | 无 | 初始 `status=new`，`assigned_to` 为空 |
| S13 | 派单 Worker 命中“指定处理人”并直接指派 U2 | Worker | Redis 派单事件 + Worker Step 0 | 无 | 最终 `GET /api/tasks/{db_id}` 中 `assigned_to=U2`，`status` 仍为 `new` |

### 阶段五：U2 登录并接收处理

| 步骤 | 前端操作 | 角色 | 接口 / 事件 | 主要入参 | 预期结果 |
|------|---------|------|-------------|---------|---------|
| S14 | U2 在登录页登录 | U2 | `POST /api/auth/login` | U2 账号 | 200，返回 token |
| S15 | U2 进入“待我处理”列表 | U2 | `POST /api/tasks/filter` | 按处理人和状态过滤 | 列表包含该工单 |
| S16 | U2 打开工单详情 | U2 | `GET /api/tasks/{db_id}?load_comments=true` | 无 | 详情显示工单状态、处理人、当前阶段 |
| S17 | U2 点击“确认同意”开始处理 | U2 | `POST /api/tasks/{db_id}/respond` | `curr_step_id` | `status=new` 变为 `in_progress`，当前阶段标记为已协商一致 |

### 阶段六：协商阶段推进

| 步骤 | 前端操作 | 角色 | 接口 / 事件 | 主要入参 | 预期结果 |
|------|---------|------|-------------|---------|---------|
| S18 | 若非最后阶段，U2 点击“当前阶段完成” | U2 | `POST /api/tasks/{db_id}/complete-step` | `next_step_id`、`curr_step_endtime` | 当前阶段前进；协商回合加 1；等待 U1 确认 |
| S19 | U1 打开工单并点击“确认同意” | U1 | `POST /api/tasks/{db_id}/respond` | `curr_step_id` | 当前阶段标记为已协商一致；状态仍为处理中 |
| S20 | 若仍有后续阶段，重复 S18-S19 | U2 + U1 | 同上 | 同上 | 阶段一路推进到最后一个节点 |
| S21 | 最后一阶段已协商一致时，U2 点击“最末阶段结束，处理完成” | U2 | 打开解决弹窗 | 解决方式文本 | 弹窗可提交解决方式 |
| S22 | U2 填写解决方式并确认完成 | U2 | `PATCH /api/tasks/{db_id}/status` | `status=resolved`、`resolution_summary` | `status=resolved`，设置 `resolved_at` |

说明：S18-S20 只在环境 `task_steps` 为该工单类型配置了多个节点时出现。若第一版只想做最小闭环，可先选择只有一个节点的工单类型，那么 S17 之后直接进入 S21。

### 阶段七：提单用户确认关闭

| 步骤 | 前端操作 | 角色 | 接口 / 事件 | 主要入参 | 预期结果 |
|------|---------|------|-------------|---------|---------|
| S23 | U1 打开已解决工单详情 | U1 | `GET /api/tasks/{db_id}?load_comments=true` | 无 | 详情显示 `status=resolved` |
| S24 | U1 点击“确认关闭” | U1 | `PATCH /api/tasks/{db_id}/status` | `status=closed` | `status=closed`，设置 `closed_at` |

## 6. 状态流转

```text
无 -> new -> in_progress -> resolved -> closed
                    ^
                    | 多节点阶段时在 in_progress 内推进，不产生新状态
```

实际前端代码中，AI 工单创建时状态为 `new`，`assigned_to` 为空；自动派单只写 `assigned_to`，不改状态；U2 首次确认后进入 `in_progress`；最后一个阶段完成后进入 `resolved`；提单用户确认后进入 `closed`。

## 7. 链路断言建议

1. S1：U1 登录成功。
2. S2-S6：同一 `session_id` 下会话和消息落库，问答可回到同一上下文。
3. S7-S8：`prepare.stage=draft_ready`、`ticket_ready=true`、`type=problem`、`project_id=Leo_test`；草稿描述包含 `[指定处理人：自动化处理人]`。
4. S10：确认提单后工单已入库，`created_by=U1`、`status=new`、`source=ai`、`project_id=Leo_test`、`assigned_to` 为空。
5. S13：等待自动派单后，`assigned_to=U2` 且 `assigned_to_name` 可解析为 U2 展示名。
6. S17：U2 首次确认后 `status=in_progress`，当前阶段协商一致。
7. S18-S20：每个协商阶段完成后，`curr_step_agreed` 先重置为 False，对方确认后恢复 True。
8. S22：`status=resolved`，且解决方式文本非空。
9. S24：`status=closed`，工单进入终态。

## 8. 报告展示建议

Allure 报告按“业务链路”作为第一层展示，链路名建议为：

```text
摇人问答提单 -> AI 派单 -> 处理人处理 -> 提单人关闭
```

每个 S 步骤在报告中显示：

| 字段 | 内容 |
|------|------|
| 中文操作 | 如“提单用户点击转工单” |
| 角色 | U1 / U2 / AI Worker |
| 接口 | 实际请求的方法、路径和主要参数 |
| 业务状态 | 从哪个状态到哪个状态 |
| 结果 | 通过 / 失败 / 跳过 |

## 9. 需要人工确认的问题

1. 固定问答文本和结构化草稿契约已确认；可控 AI 回复的具体实现方式待后端/AI 负责人确认。
2. 已确认：第一版采用 `problem` 工单类型，完整走 3 个阶段（初步诊断 / 临时解决 / 最终解决）。
3. 自动清理的接口和权限仍未确认，本规范不把清理动作写成断言。

## 10. 依据源码

只读阅读文件：

- `frontend/src/shared/components/ChatPanel.tsx`
- `frontend/src/api/ai.ts`
- `frontend/src/api/conversation.ts`
- `frontend/src/api/ticket.ts`
- `frontend/src/pages/call/CallView.tsx`
- `frontend/src/pages/tasks/TasksView.tsx`
- `frontend/src/pages/tasks/TaskDetailPage.tsx`
- `backend/app/modules/tasks/api/task.py`
- `ai/api/router.py`
- `ai/agents/AiDiagnosisPlatform/pipeline.py`
- `ai/core/task_adapter.py`

以上文件仅用于梳理，未做任何修改。
