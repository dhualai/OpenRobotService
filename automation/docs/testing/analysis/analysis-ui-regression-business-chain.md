# 微信 H5 业务链路 UI 回归 - 只读分析

> 状态：分析完成，等待人工 review
> 日期：2026-09-19
> 范围：本地自动化回归，测试环境执行
> 业务链路：U1 提问提单 -> 自动派单 U2 -> U2 处理并解决 -> U1 确认关闭
> 约束：本阶段不改业务代码、不改测试代码、不连接生产环境

## 1. 功能点

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 本地正式打包前端 | 在本机构建并运行前端正式产物，不使用测试环境已部署页面 | P0 |
| 本地 API 代理 | `/api/ai` 转发到自动化 AI `9411`，其他 `/api` 转发到测试后端 `9400` | P0 |
| 账号密码登录 | U1/U2 使用测试账号登录，不执行真实微信 OAuth | P0 |
| 可控 AI 回复 | 仅对测试用户、`sess_auto_` 会话和固定问题返回预设草稿 | P0 |
| 独立自动化 AI | `9411` 只提供 API，不启动诊断、派单、解决方式和知识沉淀 Worker | P0 |
| U1 固定问答提单 | 浏览器真实输入、发送、转工单和确认提交 | P0 |
| 自动派单 U2 | 通过真实测试环境派单逻辑将工单指派给 U2 | P0 |
| U2 搜索并处理 | 从前端系统任务列表搜索本次工单并推进处理阶段 | P0 |
| U2 提交已解决 | 填写解决方式，提交 `resolved` | P0 |
| U1 确认关闭 | U1 在系统任务详情页提交 `closed` | P0 |
| 双浏览器会话 | U1、U2 使用独立 Browser Context，不共享登录态 | P0 |
| 实际网络捕获 | 记录浏览器真实发出的接口顺序、状态码和响应数据 | P0 |
| 中文 Allure 报告 | 一个场景用例内部按角色展开中文业务步骤 | P0 |
| 步骤截图 | 关键步骤和失败现场自动附加到 Allure | P0 |
| 数据清理 | 使用管理员账号尽力清理本次测试工单、评论和会话 | P1 |
| 单接口 Smoke | 保留登录、摇人问答、工单相关稳定 Smoke，与场景用例分类展示 | P1 |
| CI 接入 | GitHub Actions、test 分支触发和工单驱动 | 本阶段不做 |

## 2. 业务流程

### 2.1 目标主链路

```text
U1 登录
  -> 新建会话
  -> 发送固定问题
  -> 获取预设 AI 回复和工单草稿
  -> 点击转工单
  -> 确认提交
  -> 后端自动派单给 U2
U2 登录
  -> 进入系统任务
  -> 搜索唯一工单号
  -> 打开详情
  -> 确认接单
  -> 推进多个处理阶段
  -> 提交已解决
U1 重新进入系统任务
  -> 搜索并打开工单
  -> 确认关闭
```

### 2.2 UI 场景步骤候选

| 步骤 | 角色 | 页面操作 | 实际接口/事件 | 页面与状态验收 |
|------|------|----------|---------------|----------------|
| S1 | U1 | 打开登录页，输入账号密码并登录 | `POST /api/auth/login`、`GET /api/admin/users/u1_auto/detail` | 进入 `/call` |
| S2 | U1 | 点击“新建会话” | 无接口 | 消息区清空，输入框可用 |
| S3 | U1 | 输入固定问题并点击发送 | `POST /api/call/conversations`、`POST /api/call/messages`、`POST /api/ai/qa/ask/stream` | 页面出现用户消息和预设 AI 回复 |
| S4 | U1 | 点击“转工单” | `POST /api/ai/qa/ticket/prepare`、`GET /api/ai/qa/ticket/steps?type=problem` | 弹出工单确认弹窗 |
| S5 | U1 | 核对草稿并点击“确认提交” | `POST /api/ai/qa/ticket/confirm`、`POST /api/call/messages` | 创建工单，页面出现工单概览气泡 |
| S6 | 后端 | 自动派单给 U2 | Worker/Redis；页面轮询 `GET /api/tasks/{id}` | 处理人为“自动化处理人”，状态为 `new` |
| S7 | U2 | 登录并点击底部“系统任务” | 登录相关接口、任务列表接口 | 进入 `/tasks` |
| S8 | U2 | 搜索唯一工单号 | `POST /api/tasks/filter` | 列表命中本次工单 |
| S9 | U2 | 点击工单卡片进入详情 | `GET /api/tasks/{id}`、`GET /api/tasks/{id}/steps` | 状态、处理人、当前阶段正确 |
| S10 | U2 | 点击“确认同意” | `POST /api/tasks/{id}/respond` | 状态变为 `in_progress`，首阶段已一致 |
| S11 | U2 | 点击“当前阶段完成”并选择下一阶段 | `POST /api/tasks/{id}/complete-step` | 进入下一阶段，等待 U1 确认 |
| S12 | U1 | 搜索、打开工单并点击“确认同意” | `POST /api/tasks/{id}/respond` | 当前阶段已一致 |
| S13 | U2 | 重新打开工单并推进到最终阶段 | `POST /api/tasks/{id}/complete-step` | 进入最终阶段，等待 U1 确认 |
| S14 | U1 | 重新打开工单并点击“确认同意” | `POST /api/tasks/{id}/respond` | 最终阶段已一致 |
| S15 | U2 | 点击“最末阶段结束，处理完成”，填写解决方式并确认 | `PATCH /api/tasks/{id}/status`，`resolved` | 状态变为 `resolved` |
| S16 | U1 | 重新进入系统任务并打开工单 | `GET /api/tasks/{id}` | 页面显示已解决和“确认关闭” |
| S17 | U1 | 点击“确认关闭” | `PATCH /api/tasks/{id}/status`，`closed` | 状态变为 `closed` |

### 2.3 交替操作同步策略

- 优先使用页面原有请求刷新、导航返回和重新打开详情同步状态。
- WebSocket 未及时更新时，允许刷新页面或返回列表重新进入。
- 不允许用直接调用业务接口替代 UI 操作。
- API/DB 校验只作为页面操作后的辅助断言。

## 3. 状态流转

### 3.1 工单主状态

```text
new -> in_progress -> resolved -> closed
```

| 源状态 | 目标状态 | 操作角色 | 说明 |
|--------|----------|----------|------|
| 无 | `new` | U1 | AI 草稿确认后创建工单 |
| `new` | `new` | Worker | 自动派单只写处理人，不改状态 |
| `new` | `in_progress` | U2 | 首次点击“确认同意” |
| `in_progress` | `in_progress` | U1/U2 | 协商阶段推进，不产生新主状态 |
| `in_progress` | `resolved` | U2 | 最终阶段完成后提交解决方式 |
| `resolved` | `closed` | U1 | 提单人确认关闭 |

### 3.2 协商阶段状态

```text
当前阶段已一致
  -> U2 当前阶段完成
  -> 下一阶段未一致
  -> U1 确认同意
  -> 当前阶段已一致
```

- `problem` 工单使用“初步诊断 / 临时解决 / 最终解决”阶段模板。
- 回归范围只覆盖合法最简路径。
- 不覆盖协商重开、升级、打回和异常回合。

### 3.3 清理状态

```text
业务完成/失败
  -> 尝试清理
  -> 清理成功
  -> 清理失败：Allure 清理告警
```

## 4. 权限控制

### 4.1 业务角色

| 角色 | 账号 | 主要能力 |
|------|------|----------|
| U1 提单用户 | `u1_auto` | 登录、提问、提单、确认关闭 |
| U2 处理人 | `u2_auto` | 登录、搜索、查看、接单、推进阶段、提交已解决 |
| 清理管理员 | 待配置专用账号 | 删除本次 `run_id` 相关工单、评论和会话 |

### 4.2 执行边界

| 能力 | 规则 |
|------|------|
| 真实微信 OAuth | 自动化不执行 |
| 账号密码登录 | 仅测试账号使用 |
| 可控回复 | 同时限制允许用户、`sess_auto_` 前缀和固定问题 |
| 真实 AI `9401` | 保持原配置和后台 Worker，不影响真实用户 |
| 自动化 AI `9411` | API-only，不启动后台 Worker |
| 测试数据 | 只允许在 `Leo_test` 项目和 `helpdesk_test` 中创建 |
| 生产环境 | 自动化绝不连接 |

## 5. 接口列表

### 5.1 前端初始化与登录

| 方法 | 路径 | 用途 | 当前状态 |
|------|------|------|----------|
| POST | `/api/auth/login` | 账号密码登录 | 已实现 |
| POST | `/api/auth/refresh` | Token 刷新 | 已实现 |
| GET | `/api/admin/users/{username}/detail` | 当前用户详情和角色 | 已实现，浏览器 Mock 契约不完整 |
| GET | `/api/ai/memory/tickets/all` | 摇人页工单角标数据 | 已实现 |
| GET | `/api/call/conversations` | 会话列表 | 已实现 |

### 5.2 U1 问答和提单

| 方法 | 路径 | 用途 | 测试目标 |
|------|------|------|----------|
| POST | `/api/call/conversations` | 创建新会话 | 真实测试后端 |
| POST | `/api/call/messages` | 保存用户/工单概览消息 | 真实测试后端 |
| POST | `/api/ai/qa/ask/stream` | SSE 问答 | 自动化 AI `9411` |
| POST | `/api/ai/qa/ticket/prepare` | 生成工单草稿 | 自动化 AI `9411` |
| GET | `/api/ai/qa/ticket/steps` | 查询处理阶段模板 | 自动化 AI 或真实后端 |
| POST | `/api/ai/qa/ticket/confirm` | 确认提单 | 真实 AI 流程与真实后端 |

### 5.3 U2 处理和 U1 关闭

| 方法 | 路径 | 用途 | 测试目标 |
|------|------|------|----------|
| POST | `/api/tasks/filter` | 前端筛选和搜索 | 真实测试后端 |
| GET | `/api/tasks/{id}` | 工单详情 | 真实测试后端 |
| GET | `/api/tasks/{id}/steps` | 协商阶段模板 | 真实测试后端 |
| POST | `/api/tasks/{id}/respond` | 确认协商节点 | 真实测试后端 |
| POST | `/api/tasks/{id}/complete-step` | 推进下一阶段 | 真实测试后端 |
| PATCH | `/api/tasks/{id}/status` | 提交已解决/确认关闭 | 真实测试后端 |
| DELETE | `/api/tasks/{id}` | 管理员清理工单 | 真实测试后端 |

### 5.4 前端依赖但第一阶段可以不触发的接口

| 方法 | 路径 | 触发条件 |
|------|------|----------|
| POST | `/api/tasks/{id}/resolution-summary` | 点击“帮我生成”解决方式 |
| GET | `/api/tasks/{id}/project-members` | 讨论区 @ 提及 |
| GET | `/api/tasks/{id}/ws` | 详情页实时更新 |
| GET | `/api/admin/projects/` | 打开项目选择器 |
| GET | `/api/admin/projects/me/relevance` | 项目选择器相关性排序 |

## 6. 风险点

| 风险 | 等级 | 影响 | 缓解措施 |
|------|------|------|----------|
| 直接启动第二个完整 AI 实例 | P0 | 重复诊断、派单、消费队列 | `9411` 必须使用 API-only 模式，禁止启动 Worker |
| 可控回复误命中真实用户 | P0 | 真实用户收到测试回复 | 用户白名单 + 会话前缀 + 固定问题三重限制，生产默认关闭 |
| 本地代理配置错误 | P0 | 请求误发测试真实 AI 或生产 | 固定配置和启动自检，打印每个 API 的实际上游 |
| 微信 OAuth 导致回归中断 | P0 | 本地无法登录 | 正式打包时关闭微信 OAuth，使用测试账号 |
| 页面缺少稳定定位器 | P1 | 中文文案调整导致失败 | 关键控件补充 `data-testid` |
| 工单关闭入口与历史详情不一致 | P1 | U1 无法按原候选链路关闭 | U1 最终关闭固定走 `/tasks/{id}` |
| 日期选择器自动化复杂 | P1 | 阶段推进不稳定 | 增加稳定 `data-testid`，明确测试日期策略 |
| 现有 `9401` 增加诊断评论 | P1 | 页面评论数据变化 | 断言只依赖主状态和阶段字段，不依赖评论 |
| WebSocket 更新不及时 | P1 | U1/U2 看到旧状态 | 允许返回列表重新打开或刷新页面 |
| 清理部分失败 | P1 | 测试数据残留 | 清理失败只告警，保留 ID 和原因，后续补偿清理 |
| SSH 隧道中断 | P1 | 链路失败或清理失败 | 前置连通性检查，有限重试，报告明确区分环境失败 |
| 真实 AI `9401` 与 `9411` 共用 Redis | P2 | Session/队列 Key 冲突 | 使用唯一 `sess_auto_`，必要时使用独立 Redis DB 或前缀 |
| 单接口 Smoke 与场景环境互相污染 | P2 | 回归结果不稳定 | Smoke 只使用只读或幂等场景，场景结束清理 |
| 本地前端源码与测试环境版本不一致 | P2 | 页面行为偏差 | 固定同一 Git commit 后再构建 |

## 7. 边界条件

| 条件 | 预期行为 |
|------|----------|
| 非测试用户请求可控 AI | 走真实 AI，不受自动化开关影响 |
| 测试用户非固定问题 | 走真实 AI 或明确失败，不返回固定草稿 |
| `session_id` 无 `sess_auto_` 前缀 | 不启用可控回复 |
| 固定问题返回需要补字段 | 当前不扩展多轮补充，判为契约不符合 |
| 自动派单超时 | 等待达到上限后失败，报告记录当前处理人 |
| 工单号搜索结果不唯一 | 失败，不按列表顺序盲选 |
| 阶段推进后对方尚未确认 | 返回列表重新打开，等待状态同步 |
| 页面显示旧状态 | 允许刷新或重新进入详情 |
| 任一业务步骤失败 | 立即停止后续 UI 操作并保存截图和网络记录 |
| 清理管理员登录失败 | 业务结果不变，记录清理告警 |
| 清理接口返回部分成功 | 记录残留工单号和原因，业务结果不变 |
| SSH 隧道不可用 | 判定环境失败，不伪装成业务失败或通过 |
| 测试产生附件 | 本阶段不覆盖附件链路 |
| AI 回复文字变化 | 不直接断言回复全文，只断言结构化草稿和后续状态 |
| 生产环境启动自动化 | 未支持，明确阻止 |

## 8. 当前实现与目标差距

### 8.1 已有能力

- Playwright 依赖、Chromium 配置和 UI Smoke 已具备。
- 登录页和“我要摇人”页面可被 Playwright 打开。
- 已有 `httpx.MockTransport` API 层完整业务链路用例。
- 已有 U1/U2 固定问题、工单草稿和状态流转接口契约。
- 已有业务链路 Allure 分类基础。
- 已有真实后端工单生命周期 API 用例。

### 8.2 主要缺口

1. 当前业务链路是 API 级 Mock 调用，不是浏览器 UI 链路。
2. 当前 `MockBackend` 是进程内 `httpx.MockTransport`，浏览器不能直接连接。
3. 当前 UI 只验证登录和打开页面，没有真实点击提单、处理和关闭。
4. 当前没有 API-only 的可控 AI 自动化入口。
5. 当前没有本地前端正式打包、反向代理和 SSH 隧道的统一启动方案。
6. 当前没有 U1/U2 双 Browser Context 的场景执行器。
7. 当前没有浏览器实际请求捕获和业务步骤关联。
8. 当前没有步骤截图、失败现场和敏感字段脱敏的完整实现。
9. 当前关键页面缺少 `data-testid`。
10. 当前历史工单详情页没有“确认关闭”，U1 必须走系统任务详情页。
11. 当前没有专用清理管理员配置和清理告警模型。
12. 当前没有 `9411` 的 API-only 启动方式和部署配置。

## 9. 已确认决策

1. 第一阶段目标为本地自动化回归，不是演示。
2. 只覆盖一条主链路，不覆盖分支。
3. 自动化只运行在测试环境，不运行在生产环境。
4. 本地构建并运行前端正式产物。
5. 本地代理连接测试后端 `9400` 和自动化 AI `9411`。
6. 使用账号密码登录，不执行微信 OAuth。
7. `9411` 为 API-only 实例，不启动后台 Worker。
8. 真实 AI `9401` 保持原样。
9. 可控回复只对测试用户、测试会话和固定问题生效。
10. 使用 `helpdesk_test`、`Leo_test`、U1/U2 和唯一 `run_id`。
11. 允许现有 `9401` 给测试工单增加诊断评论。
12. 页面状态未实时更新时，允许刷新或重新打开同步。
13. 清理失败只产生告警，不改变业务结论。
14. Allure 分为“场景用例”和“单接口用例”。
15. 接口顺序由浏览器实际捕获。
16. 业务内容中文，敏感信息脱敏。
17. 第一阶段不接 GitHub Actions、分支触发和工单驱动。

## 10. 建议实施优先级

| 优先级 | 工作项 |
|--------|--------|
| P0 | 设计 URL、凭证、代理、隧道和运行环境边界 |
| P0 | 设计 API-only 可控 AI `9411` 及安全开关 |
| P0 | 设计本地前端正式打包和反向代理启动方式 |
| P0 | 设计 U1/U2 双会话 Playwright 场景结构与断言 |
| P0 | 设计实际网络捕获、截图、脱敏和 Allure 步骤模型 |
| P0 | 设计稳定清理和清理告警模型 |
| P1 | 设计单接口 Smoke 与场景用例的联合报告 |
| P1 | 设计关键控件 `data-testid` 清单 |
| P2 | 设计本地一键运行脚本和文档 |
| P2 | 评估后续 CI 和测试环境触发方式 |

## 11. 依据源码

只读阅读：

- `automation/docs/business-flows/call-qa-to-ticket-close.md`
- `automation/docs/design-business-chain-execution-layer.md`
- `automation/docs/design-ui-automation-selectors.md`
- `automation/tests/business_chain/test_call_to_ticket_close.py`
- `automation/tests/real/test_ticket_lifecycle_real.py`
- `automation/tests/ui/test_login_smoke.py`
- `automation/tests/ui/test_call_ui_smoke.py`
- `automation/src/mocks/backend_mock.py`
- `frontend/src/pages/Login.tsx`
- `frontend/src/shared/utils/authGuard.tsx`
- `frontend/src/shared/components/ChatPanel.tsx`
- `frontend/src/shared/components/MainLayout.tsx`
- `frontend/src/shared/components/StepNegotiationCard.tsx`
- `frontend/src/shared/hooks/useStepNegotiation.ts`
- `frontend/src/shared/hooks/useResolveTicket.ts`
- `frontend/src/pages/tasks/TasksView.tsx`
- `frontend/src/pages/tasks/TaskDetailPage.tsx`
- `frontend/src/pages/call/TicketDetailPage.tsx`
- `frontend/src/config/api.ts`
- `frontend/vite.config.ts`
- `backend/app/modules/tasks/api/task.py`
- `backend/app/modules/tasks/services/ticket_service.py`
- `ai/run.py`
- `ai/config.py`
- `ai/core/llm.py`
- `ai/agents/AiTaskPlatform/services/diagnosis_worker.py`
- `ai/agents/AiDiagnosisPlatform/assigner/pipeline/worker.py`

## 12. 下一步

分析阶段到此停止。下一步应在人工确认本分析后，单独输出框架设计文档，明确：

- 文件级模块划分。
- 运行链路和端口。
- 可控 AI 请求/响应契约。
- Playwright 场景和页面对象结构。
- 网络捕获、截图、脱敏和 Allure 报告结构。
- 清理策略。
- 一次性实施边界和验证方式。

未经人工确认，不进入代码实现。
