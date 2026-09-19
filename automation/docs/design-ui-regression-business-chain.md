# 微信 H5 业务链路 UI 回归框架设计

> 状态：设计稿，等待人工确认
> 日期：2026-09-19
> 依据：`automation/docs/testing/analysis/analysis-ui-regression-business-chain.md`
> 目标：本地执行“U1 提问提单 -> U2 处理解决 -> U1 确认关闭”真实测试环境 UI 回归
> 约束：设计阶段不修改业务代码或测试代码

## 1. 目标

建立一套可重复执行的本地自动化回归框架，使用真实前端页面、真实测试后端、
测试数据库和独立可控 AI，验证以下唯一主链路：

```text
U1 登录
-> 新建会话
-> 固定问答
-> 转工单并确认提交
-> 自动派单 U2
-> U2 搜索工单
-> U2 接单并推进阶段
-> U2 提交已解决
-> U1 确认关闭
```

回归完成后生成中文 Allure 报告，报告中的接口顺序和结果来自浏览器实际网络流量。

## 2. 不在本设计范围

1. 不接 GitHub Actions、test 分支触发和工单驱动。
2. 不连接生产环境。
3. 不执行真实微信 OAuth。
4. 不验证 AI 回答质量。
5. 不覆盖转派、催办、挂起、退回和异常分支。
6. 不改造真实 AI `9401` 的运行方式和后台 Worker。
7. 不直接修改 `ai/`、`backend/`、`frontend/` 业务代码。
8. 不把清理失败作为业务链路失败。

## 3. 总体架构

```text
本机
├── 本地前端正式产物 frontend/dist
├── 本地 UI 回归 Gateway
│   ├── 静态页面
│   ├── /api/ai/* -> 127.0.0.1:19411
│   └── 其他 /api/* -> 127.0.0.1:19400
├── Playwright U1 Context
├── Playwright U2 Context
└── pytest + Allure
        │
        │ SSH tunnels
        ▼
测试服务器
├── 测试后端 9400
├── 真实 AI 9401，保持原样
├── 自动化 AI 9411，API-only
└── 受控 LLM Replay 9410，仅 9411 内部使用
        │
        ▼
MySQL helpdesk_test + Redis
```

### 3.1 关键原则

- 浏览器执行真实 UI 操作。
- `/api/ai` 的请求进入自动化 AI `9411`，不进入真实 AI `9401`。
- 其他业务请求进入真实测试后端 `9400`。
- 自动化 AI 使用真实 AI 管线，但模型调用由确定性 Replay 服务响应。
- 自动化 AI 不加载 `ai.run` 的 lifespan，因此不启动后台 Worker。
- 真实 AI `9401` 不改配置、不重启、不暴露给自动化。

## 4. 模块划分

### 4.1 自动化 AI 模块

建议目录：

```text
automation/src/controlled_ai/
├── __init__.py
├── config.py
├── app.py
├── replay.py
├── llm_server.py
└── tests/
    ├── __init__.py
    └── test_replay.py
```

职责：

| 文件 | 职责 |
|------|------|
| `config.py` | 读取端口、上游地址、允许用户、Session 前缀和运行模式 |
| `app.py` | 构建 API-only FastAPI 应用，挂载 AI 路由，不执行 lifespan Worker |
| `replay.py` | 加载确定性模型响应，校验固定问题和注入 `run_id` |
| `llm_server.py` | 提供 OpenAI 兼容 `/v1/chat/completions` 和 `/v1/responses` |
| `test_replay.py` | 验证请求匹配、响应协议、非固定问题拒绝和并发隔离 |

### 4.2 本地运行模块

建议目录：

```text
automation/src/ui_regression/
├── __init__.py
├── config.py
├── gateway.py
├── capture.py
├── cleanup.py
├── page_actions.py
└── tests/
    ├── __init__.py
    ├── test_gateway.py
    └── test_capture.py
```

职责：

| 文件 | 职责 |
|------|------|
| `config.py` | 读取端口、测试环境地址、账号环境变量、超时和运行标记 |
| `gateway.py` | 提供本地前端静态服务，并代理业务 API 和自动化 AI |
| `capture.py` | 捕获 Playwright 请求/响应、脱敏、截图和 Allure 附件 |
| `cleanup.py` | API-first 清理工单、评论和会话，输出清理告警 |
| `page_actions.py` | 封装登录、问答、提单、搜索、处理和关闭页面动作 |
| `test_gateway.py` | 验证静态文件、API 转发、SSE 透传和上游选择 |
| `test_capture.py` | 验证脱敏、响应截断和 Step 归类 |

### 4.3 Playwright 场景模块

建议目录：

```text
automation/tests/ui/
├── __init__.py
├── conftest.py
├── test_login_smoke.py
├── test_call_ui_smoke.py
└── test_call_qa_to_ticket_close_regression.py
```

职责：

| 文件 | 职责 |
|------|------|
| `conftest.py` | Playwright 生命周期、U1/U2 Context、运行标记、清理夹具 |
| `test_call_qa_to_ticket_close_regression.py` | 唯一的 S1-S17 业务链路场景 |
| 现有两个 Smoke | 保留为单接口/UI Smoke 基线，不与场景断言混用 |

### 4.4 运行脚本和配置

建议新增：

```text
automation/config/ui_regression.local.yaml
automation/scripts/run-ui-regression.ps1
```

职责：

| 文件 | 职责 |
|------|------|
| `ui_regression.local.yaml` | 非敏感端口、超时、测试项目和数据标记规则 |
| `run-ui-regression.ps1` | 构建前端、启动隧道、启动 Gateway、执行 pytest、生成报告 |

敏感账号、密码和 SSH 信息只从环境变量或本机未跟踪配置读取。

## 5. 运行链路

### 5.1 前端启动

```powershell
npm ci --prefix frontend
$env:VITE_WECHAT_LOGIN_ENABLED = "false"
$env:VITE_WECHAT_JSSDK_ENABLED = "false"
npm run build --prefix frontend
```

使用默认 base `/`，不生成 `/t/app/` 或 `/p/app/` 部署包。

### 5.2 SSH 隧道

建议本机端口：

| 本机 | 测试服务器 | 用途 |
|------|------------|------|
| `19400` | `127.0.0.1:9400` | 真实业务后端 |
| `19411` | `127.0.0.1:9411` | 自动化 AI |

不建立到真实 AI `9401` 的自动化隧道。

### 5.3 自动化 AI 启动

`9411` 进程建议使用以下环境：

```env
PORT=9411
LLM_BACKEND=relay
RELAY_BASE_URL=http://127.0.0.1:9410/v1
RELAY_API_KEY=automation
RELAY_MODEL=automation-fixed
RELAY_FALLBACK_MODELS=
AUTOMATION_CONTROLLED_REPLY=1
AUTOMATION_ALLOWED_USERS=u1_auto
AUTOMATION_SESSION_PREFIX=sess_auto_
BACKEND_BASE_URL=http://127.0.0.1:9400
```

进程只加载 FastAPI 路由，不执行：

- DB schema 自愈。
- Embedding 预热。
- 知识库入库。
- 诊断 Worker。
- 派单 Worker。
- 解决方式总结 Worker。
- 知识沉淀 Worker。

### 5.4 本地 Gateway

默认监听 `127.0.0.1:4173`：

```text
GET /                -> frontend/dist
GET /call            -> frontend/dist/index.html
GET /tasks           -> frontend/dist/index.html
/api/ai/*            -> http://127.0.0.1:19411
其他 /api/*          -> http://127.0.0.1:19400
```

Gateway 启动时必须执行上游健康检查：

- `GET http://127.0.0.1:19400/api/health`
- `GET http://127.0.0.1:19411/health`

任一失败时，场景不启动，报告标记为“环境访问失败”。

## 6. 可控 AI 契约

### 6.1 固定问题

```text
我要在项目 Leo_test（摇人吧服务号-测试）提一个 problem 工单。
指定处理人：自动化处理人。
标题：自动化链路验证-{run_id}。
问题：机器人无法启动，故障码 E1001，已尝试重启仍无效。
请直接生成工单草稿，不要继续追问。
```

### 6.2 启用条件

只有同时满足以下条件才使用 Replay：

- 请求来自允许的测试用户。
- `session_id` 以 `sess_auto_` 开头。
- 用户文本严格匹配固定问题模板。
- 当前进程启用 `AUTOMATION_CONTROLLED_REPLY=1`。

其他请求直接失败并记录“可控回复契约不匹配”，不能静默回退真实模型。

### 6.3 草稿契约

```text
prepare.stage == draft_ready
prepare.ticket_ready == true
draft.type == problem
draft.project_id == Leo_test
draft.title == 自动化链路验证-{run_id}
draft.description contains "[指定处理人：自动化处理人]"
draft.curr_step_id 为 problem 首阶段 ID
```

### 6.4 Pipeline 约束

- `ask/stream`、`prepare`、`confirm` 使用真实 AI 管线代码。
- 模型调用由 `9410` Replay 服务返回。
- `confirm` 由真实 AI 管线调用真实测试后端建单。
- 不直接跳过 AI 状态机，不通过测试脚本直接创建工单。
- Replay 数据放在 `automation/testdata/controlled_ai/`，只保留测试数据。

## 7. Playwright 场景设计

### 7.1 Browser Context

| Context | 用户 | 生命周期 |
|---------|------|----------|
| U1 | `u1_auto` | 从登录到确认关闭 |
| U2 | `u2_auto` | 从登录到提交已解决 |

两个 Context 独立 Cookie、LocalStorage 和 Token。

### 7.2 页面动作

`page_actions.py` 提供以下高层动作：

```text
login(page, username, password)
start_new_conversation(page)
send_fixed_question(page, question)
open_ticket_draft(page)
confirm_ticket(page, expected_title)
open_system_tasks(page)
search_ticket(page, ticket_id)
open_ticket(page, ticket_id)
confirm_current_step(page)
complete_current_step(page, next_step_name, end_time)
resolve_ticket(page, resolution)
close_ticket(page)
```

动作只负责页面交互和页面断言，业务状态由后续 API/DB 校验补充。

### 7.3 等待策略

- 优先等待 Playwright `expect` 的元素和状态。
- 同时等待关键接口响应完成。
- 禁止固定 `sleep` 作为主要等待方式。
- 自动派单最长等待 90 秒。
- 页面状态同步允许返回列表重新打开或刷新。
- AI 追问、长时间无响应或超时立即失败并记录最后页面状态。

### 7.4 日期选择策略

- 为阶段结束时间输入增加 `data-testid`。
- 默认选择当前日期后 7 天的 18:00。
- 优先通过日期控件真实交互；若组件强制只读，则使用该组件支持的键盘输入和确认动作。
- 不允许通过直接改 React state 或业务接口绕过日期选择。

## 8. Allure 报告设计

### 8.1 分类

```text
场景用例
└── 摇人问答提单 -> 处理人处理 -> 提单人关闭

单接口用例
└── 登录 / 摇人问答 / 工单 Smoke
```

### 8.2 场景结构

```text
Feature: 微信 H5 自动化回归
Story: 摇人问答到工单关闭
Test: 测试环境：U1 提单，U2 处理，U1 关闭
```

每个 S 步骤使用 Allure Step：

```text
S03 [U1] 发送固定问题
S05 [U1] 确认提交工单
S06 [Worker] 等待自动派单给 U2
S10 [U2] 确认接单 new -> in_progress
S15 [U2] 提交已解决 in_progress -> resolved
S17 [U1] 确认关闭 resolved -> closed
```

### 8.3 每步附件

每步至少附加：

- 页面截图。
- 实际接口序列 JSON。
- 关键请求参数。
- 关键响应字段。
- 页面状态断言。
- 业务状态变化。
- 步骤耗时。

失败时额外附加：

- 完整页面截图。
- 当前 URL。
- 最后 20 条网络事件。
- Playwright Trace，可选但推荐。
- 失败前后工单状态。

### 8.4 脱敏

以下字段必须替换为 `[REDACTED]`：

- `password`
- `access_token`
- `refresh_token`
- `token`
- `authorization`
- `cookie`
- `set-cookie`

响应正文单条最多保留 20 KB。

## 9. 数据清理设计

### 9.1 唯一标记

```text
run_id = ui-{yyyyMMddHHmmss}-{6位随机值}
session_id = sess_auto_ui_{run_id}
ticket_title = AUTO-UI-{run_id}
```

### 9.2 清理顺序

1. 删除会话。
2. 删除工单评论及相关子记录，优先通过管理员接口。
3. 删除工单，优先通过管理员接口。
4. 记录每一步清理结果。

### 9.3 失败语义

- 业务测试结果已确定后执行清理。
- 清理异常只产生 `CleanupWarning`。
- Allure 中附加待清理 ID、接口响应和错误信息。
- 清理失败不改变 pytest 的业务通过/失败状态。
- 管理员硬删除接口异常时保留记录，后续人工或补偿脚本处理。

## 10. 前端 data-testid 设计

### 10.1 登录

| testid | 文件 |
|--------|------|
| `login-username` | `frontend/src/pages/Login.tsx` |
| `login-password` | `frontend/src/pages/Login.tsx` |
| `login-submit` | `frontend/src/pages/Login.tsx` |

### 10.2 摇人问答

| testid | 文件 |
|--------|------|
| `chat-new-conversation` | `ChatPanel.tsx` |
| `chat-input` | `ChatPanel.tsx` |
| `chat-send` | `ChatPanel.tsx` |
| `chat-transfer-ticket` | `ChatPanel.tsx` |
| `chat-ticket-draft-modal` | `ChatPanel.tsx` |
| `chat-ticket-title` | `ChatPanel.tsx` |
| `chat-ticket-description` | `ChatPanel.tsx` |
| `chat-ticket-confirm` | `ChatPanel.tsx` |
| `chat-ticket-overview-{id}` | `ChatPanel.tsx` |

### 10.3 系统任务

| testid | 文件 |
|--------|------|
| `tasks-search` | `TasksView.tsx` |
| `task-card-{id}` | `TasksView.tsx` |
| `task-status` | `TaskDetailPage.tsx` |
| `task-assignee` | `TaskDetailPage.tsx` |
| `task-current-step` | `TaskDetailPage.tsx` |
| `task-accept` | `StepNegotiationCard.tsx` |
| `task-complete-step` | `StepNegotiationCard.tsx` |
| `task-step-next` | `StepNegotiationCard.tsx` |
| `task-step-endtime` | `StepNegotiationCard.tsx` |
| `task-step-submit` | `StepNegotiationCard.tsx` |
| `task-resolve` | `StepNegotiationCard.tsx` |
| `task-resolution-summary` | `TaskDetailPage.tsx` |
| `task-resolve-confirm` | `TaskDetailPage.tsx` |
| `task-close` | `TaskDetailPage.tsx` |

`data-testid` 不改变样式、文案和业务行为。

## 11. 配置与环境变量

非敏感配置：

```yaml
backend:
  local_port: 19400
  remote_port: 9400
automation_ai:
  local_port: 19411
  remote_port: 9411
frontend:
  local_port: 4173
timeouts:
  auto_assign_seconds: 90
  step_seconds: 20
test_data:
  project_id: Leo_test
  ticket_type: problem
  run_prefix: AUTO-UI-
```

本机环境变量：

```env
UI_REGRESSION_U1_USERNAME=u1_auto
UI_REGRESSION_U1_PASSWORD=
UI_REGRESSION_U2_USERNAME=u2_auto
UI_REGRESSION_U2_PASSWORD=
UI_REGRESSION_CLEANUP_USERNAME=
UI_REGRESSION_CLEANUP_PASSWORD=
```

SSH 配置继续复用受限环境变量，不写入仓库。

## 12. 实现步骤

每个阶段独立实现、验证和 review，单阶段文件数不超过 10。

### 阶段 1：可控 AI Replay

文件：

- `automation/src/controlled_ai/__init__.py`
- `automation/src/controlled_ai/config.py`
- `automation/src/controlled_ai/replay.py`
- `automation/src/controlled_ai/llm_server.py`
- `automation/src/controlled_ai/app.py`
- `automation/src/controlled_ai/tests/__init__.py`
- `automation/src/controlled_ai/tests/test_replay.py`
- `automation/testdata/controlled_ai/call_ticket_close.json`

验证：

- Replay 服务单测通过。
- 非固定问题返回明确失败。
- `9411` 启动后无后台 Worker 日志。
- `9411` 的 `confirm` 能在测试环境创建真实测试工单。

### 阶段 2：本地 Gateway

文件：

- `automation/src/ui_regression/__init__.py`
- `automation/src/ui_regression/config.py`
- `automation/src/ui_regression/gateway.py`
- `automation/src/ui_regression/tests/__init__.py`
- `automation/src/ui_regression/tests/test_gateway.py`

验证：

- 本地静态页面可打开。
- `/api/ai` 命中 `19411`。
- 其他 `/api` 命中 `19400`。
- SSE 可以完整返回。
- 上游不可用时明确失败。

### 阶段 3：前端定位器

文件：

- `frontend/src/pages/Login.tsx`
- `frontend/src/shared/components/ChatPanel.tsx`
- `frontend/src/shared/components/StepNegotiationCard.tsx`
- `frontend/src/pages/tasks/TasksView.tsx`
- `frontend/src/pages/tasks/TaskDetailPage.tsx`

验证：

- 前端单测通过。
- 页面样式和业务行为无变化。
- 所有关键定位符可被 Playwright 获取。

### 阶段 4：场景执行与报告

文件：

- `automation/src/ui_regression/capture.py`
- `automation/src/ui_regression/cleanup.py`
- `automation/src/ui_regression/page_actions.py`
- `automation/src/ui_regression/tests/test_capture.py`
- `automation/tests/ui/conftest.py`
- `automation/tests/ui/test_call_qa_to_ticket_close_regression.py`

验证：

- U1 提单 Smoke 通过。
- U1/U2 完整链路通过。
- 每步实际接口和截图进入 Allure。
- 失败时立即停止并保留现场。
- 清理失败只告警。

### 阶段 5：一键运行

文件：

- `automation/config/ui_regression.local.yaml`
- `automation/scripts/run-ui-regression.ps1`
- `automation/docs/UI_REGRESSION.md`

验证：

- 从干净终端一条命令启动。
- 连续运行两次结果稳定。
- 报告自动生成。
- 文档可由其他成员按步骤复现。

## 13. 测试策略

### 13.1 框架单测

- Gateway 路由和上游健康检查。
- Replay 匹配和运行标记替换。
- 网络捕获脱敏和截断。
- CleanupWarning 不改变业务结果。

### 13.2 UI Smoke

- U1 登录。
- 打开发消息页面。
- 新建会话。
- 发送固定问题并可点击转工单。

### 13.3 场景回归

- 完整 S1-S17。
- 浏览器实际网络请求顺序。
- 页面截图和业务状态。
- 连续执行两次。

### 13.4 单接口 Smoke

- 登录。
- 摇人会话和消息。
- 工单查询、阶段和状态相关稳定接口。

## 14. 验收标准

- 自动化只连接测试环境。
- 真实 AI `9401` 未被修改或代替。
- `9411` 未启动任何后台 Worker。
- 微信 OAuth 未进入自动化链路。
- U1/U2 使用独立 Browser Context。
- 浏览器实际完成 S1-S17。
- 关键页面操作全部有截图。
- Allure 中每步显示角色、中文操作、接口和状态。
- 接口列表来自真实网络捕获。
- 敏感信息已脱敏。
- 场景失败会立即停止并保留现场。
- 清理失败只产生告警。
- 连续两次运行结果一致。
- 报告分为“场景用例”和“单接口用例”。

## 15. 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| 真实 AI 调用次数变化导致 Replay 不稳定 | P0 | Replay 契约单测；变更时更新固定轨迹并 review |
| `9411` 误启动 Worker | P0 | 使用 API-only 入口，启动日志和健康接口校验 Worker 数量为 0 |
| Gateway 转发错环境 | P0 | 启动时打印上游并检查 `/health`，配置中禁止生产地址 |
| 管理员硬删除接口仍有 500 | P1 | 清理只告警，保留 ID；DB 补偿清理另行授权 |
| 日期选择器不稳定 | P1 | 增加 testid 和键盘输入策略；验证后再扩展场景 |
| 9401 诊断 Worker 增加评论 | P1 | 不依赖评论内容，只断言主状态和阶段字段 |
| 页面实时更新延迟 | P1 | 返回列表重新打开或刷新页面 |
| 两次运行残余数据互相干扰 | P1 | 唯一 `run_id`、标题精确匹配、搜索后校验 ID |
| 前端定位器变更 | P1 | 固定 testid，关键动作不依赖中文文案 |
| 本地构建与测试环境版本不一致 | P2 | 运行前校验 Git commit 并记录在 Allure |

## 16. 待确认事项

1. `9411` 的 API-only 进程由 `systemd`、Docker 还是手工脚本维护。
2. `9411` 与 `9401` 是否共用 Redis；若共用，是否需要独立 DB 或前缀。
3. 管理员硬删除接口当前 500 是否继续修复。
4. 第一阶段是否只执行场景用例，还是本次同时执行单接口 Smoke。
5. 是否接受调试时保留失败 Trace，正式本地回归默认只保留截图和网络 JSON。

## 17. 下一步

本设计经人工确认后，按第 12 节阶段顺序实施。每次只做一个阶段，阶段完成后：

1. 运行对应单测。
2. 运行目标测试。
3. 生成 Allure。
4. 更新 worklog。
5. 等待下一阶段确认。
