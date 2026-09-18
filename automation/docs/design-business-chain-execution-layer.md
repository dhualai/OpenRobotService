# 业务链路执行层设计（候选）

> 状态：设计稿，待人工确认后再实现
> 日期：2026-09-11
> 范围：真实后端“摇人问答 -> 提单 -> 派单 -> 处理 -> 关闭”
> 约束：不修改 `frontend/`、`backend/`、`ai/` 业务逻辑

## 1. 目标

把已确认的 S1-S24 业务链路规范落到现有 `pytest + ApiClient + Allure` 框架内，形成一条可在真实后端执行、报告可读、可进入 CI 的业务链路测试。

第一版只覆盖：

```text
U1 登录 -> 摇人问答 -> 转工单 -> 确认提单
-> 自动派单给 U2
-> U2 登录 -> 接单 -> 推进 problem 3 个阶段 -> 提交已解决
-> U1 确认关闭
```

链路步骤和接口细节以
`automation/docs/business-flows/call-qa-to-ticket-close.md`
为唯一业务规范，本设计只定义执行层如何承载这条规范。

## 2. 不在本设计范围

1. 不验证 AI 回答质量，只验证固定问答能否驱动业务流程。
2. 不做 UI 自动化，不驱动浏览器；前端操作映射为对应接口调用。
3. 不检查 8400/8401 实例。
4. 不在 PR 中执行真实后端链路，不向 fork PR 暴露 secrets。
5. 不直接写数据库造数或清数；清理必须有明确接口或独立授权脚本。

## 3. 现有能力复用

| 现有能力 | 复用方式 |
|---|---|
| `ApiClient` | 沿用 httpx、超时、重试、请求/响应 Allure 附件 |
| `assert_status_code` / `assert_dict_contains_subset` | 单步 HTTP 与字段断言 |
| `pytest-asyncio` | 整条链路使用异步测试 |
| `allure-pytest` | 按业务链路、角色、操作、接口、状态生成步骤 |
| GitHub Actions + 受限 SSH key | 复用已通过的 `real-access-check.yml` 访问方式 |
| 环境变量 + GitHub Secrets | 注入真实后端地址和 U1/U2 凭据 |

不引入 Tavern、Robot Framework、HttpRunner，也不新增第二套用例数据源。

## 4. 模块划分

建议新增目录：

```text
automation/src/business_chain/
├── __init__.py
├── session.py       # U1/U2 会话和 token
├── reporting.py     # 业务步骤与状态流转报告
├── sse.py           # SSE 流解析
└── call_ticket.py   # 摇人提单到关闭的领域动作

automation/tests/real/
├── conftest.py
└── test_call_to_ticket_close.py

.github/workflows/
└── real-business-chain.yml
```

总文件数控制在 8 个以内，符合“一次只实现一个模块”的边界。

### 4.1 `session.py`

职责：

- 封装 `RoleSession`：角色、用户名、密码、`ApiClient`、access token、请求头。
- 登录后缓存 token；401 时只做一次重新登录，不自动绕过权限断言。
- 不把密码或 token 写入日志、Allure 附件或错误信息。

建议接口：

```python
class RoleSession:
    role: str
    username: str
    headers: dict[str, str]

    async def login(self) -> None: ...
    async def get(self, path: str, **kwargs): ...
    async def post(self, path: str, **kwargs): ...
    async def patch(self, path: str, **kwargs): ...
```

### 4.2 `reporting.py`

职责：

- 把每个 S 步骤包装为一个 Allure step。
- 每个步骤至少展示：
  - 中文操作
  - 角色 U1 / U2 / Worker
  - HTTP 方法和路径
  - 业务状态变化
  - 通过 / 失败 / 跳过
- 额外附加一份结构化 `Business Step` JSON，供报告查看。

示例报告标题：

```text
S10 [U1] 确认提单 -> POST /api/ai/qa/ticket/confirm -> new
S17 [U2] 确认接单 -> POST /api/tasks/{id}/respond -> new -> in_progress
S22 [U2] 提交已解决 -> PATCH /api/tasks/{id}/status -> in_progress -> resolved
S24 [U1] 确认关闭 -> PATCH /api/tasks/{id}/status -> resolved -> closed
```

`ApiClient` 已有 Request / Response 附件，本模块只补充业务语义，不重复实现 HTTP 日志。

### 4.3 `sse.py`

职责：

- 调用真实前端同源接口 `/api/ai/qa/ask/stream`。
- 解析 SSE 的 `event:` / `data:` 行，识别：
  - `token`
  - `status`
  - `result`
  - `error`
  - `done`
- 提供固定的最大等待时间，超时立即失败并在报告中记录最后收到的事件。
- 将完整 SSE 事件序列作为 Allure 附件保存。

现有 `ApiClient` 只封装普通单请求，不暴露流式上下文。实现阶段需要增加最小流式读取能力，但不改变现有 API 行为。

### 4.4 `call_ticket.py`

职责：承载业务动作，不直接写 pytest 断言细节。

建议按阶段拆分：

```python
async def create_qa_conversation(u1: RoleSession) -> dict
async def run_fixed_qa(u1: RoleSession, session_id: str) -> str
async def prepare_ticket(u1: RoleSession, session_id: str) -> dict
async def get_ticket_steps(u1: RoleSession, ticket_type: str) -> list[dict]
async def confirm_ticket(u1: RoleSession, session_id: str, overrides: dict) -> int
async def wait_for_assignment(session: RoleSession, ticket_id: int, assignee: str) -> dict
async def responder_accept(session: RoleSession, ticket_id: int, step_id: int) -> dict
async def complete_stage(session: RoleSession, ticket_id: int, next_step_id: int) -> dict
async def resolve_ticket(session: RoleSession, ticket_id: int, summary: str) -> dict
async def close_ticket(session: RoleSession, ticket_id: int) -> dict
```

### 4.5 `tests/real/conftest.py`

新增 fixtures：

- `real_api_config`：从环境变量构造真实后端配置。
- `u1_session`：`u1_auto` 会话。
- `u2_session`：`u2_auto` 会话。
- `chain_marker`：本次运行的唯一工单标记，便于识别和后续清理。
- `business_chain_report`：链路级报告上下文。

环境变量：

```text
USE_MOCK=0
REAL_API_BASE_URL=http://localhost:9400
REAL_U1_USERNAME=u1_auto
REAL_U1_PASSWORD=<GitHub Secret>
REAL_U2_USERNAME=u2_auto
REAL_U2_PASSWORD=<GitHub Secret>
```

真实账号密码只允许来自环境变量 / GitHub Secrets，不写入代码和文档。

### 4.6 `test_call_to_ticket_close.py`

只保留一条 `@pytest.mark.e2e` 测试：

```text
test_call_qa_to_ticket_close
```

测试内部按 S1-S24 顺序调用 `call_ticket.py`，断言点来自链路规范第 7 节：

- 提单前：会话、SSE 事件、draft 状态和必填字段。
- 创建后：`created_by=U1`、`source=ai`、`status=new`、`assigned_to=U2`。
- 处理中：`status=in_progress`，当前阶段和协商状态正确。
- 已解决：`status=resolved`，解决方式非空。
- 关闭后：`status=closed`，设置关闭时间。

报告只展示这一条“业务链路”用例，底层接口断言作为该用例内部的步骤。

### 4.7 `real-business-chain.yml`

触发边界：

```yaml
on:
  workflow_dispatch:
  push:
    branches: [test]
```

不配置 `pull_request`。

Job：

1. 建立真实环境 SSH 隧道。
2. 调用 `/api/health` 做前置检查。
3. 安装 `automation/`。
4. 执行 `pytest automation/tests/real/test_call_to_ticket_close.py -m e2e`。
5. 上传 Allure 结果并生成 HTML 报告。
6. 输出报告链接和本次运行结论。

访问层失败时，业务链路不再执行，报告明确标记为“环境访问失败”，避免误判为业务失败。

### 4.8 固定问答与草稿契约

已确认第一版固定问答：

```text
U1：我要在项目 Leo_test（摇人吧服务号-测试）提一个 problem 工单。
    指定处理人：自动化处理人。
    标题：自动化链路验证-{run_id}。
    问题：机器人无法启动，故障码 E1001，已尝试重启仍无效。
    请直接生成工单草稿，不要继续追问。

AI：工单草稿已生成。
    类型：problem
    项目：Leo_test（摇人吧服务号-测试）
    指定处理人：自动化处理人
    标题：自动化链路验证-{run_id}
    可以直接转工单。
```

执行层不直接断言 AI 回复全文，只断言稳定结构化契约：

```text
prepare.stage == draft_ready
prepare.ticket_ready == true
draft.type == problem
draft.project_id == Leo_test
draft.title contains run_id
draft.description contains "[指定处理人：自动化处理人]"
```

如果可控回复仍返回 `need_fields`，测试按固定补充序列继续，但最终结果必须收敛到上述草稿契约。

## 5. 执行流程

### 阶段一：U1 问答和提单

1. U1 登录。
2. 创建会话，写入 `ai_session_id`。
3. 调用 SSE 固定问答，按预设脚本补充信息。
4. 调 `prepare`，直到 `draft_ready` 或 `review`。
5. 调 `ticket/steps` 获取 `problem` 的 3 个阶段。
6. 调 `confirm` 创建工单，记录 `db_id`。

断言：`type=problem`、`project_id=Leo_test`、草稿描述包含 `[指定处理人：自动化处理人]`，工单状态为 `new`。

### 阶段二：等待自动派单

1. 轮询 `GET /api/tasks/{db_id}`。
2. 等待 `assigned_to` 命中 U2。
3. 超时上限建议 90 秒，轮询间隔 5 秒。

断言：`assigned_to=U2`，状态仍为 `new`。

### 阶段三：U2 接收和处理

1. U2 登录。
2. 通过任务筛选接口确认工单在待处理列表。
3. 调 `respond`，第一次响应使 `new -> in_progress`。
4. 按 `problem` 的 3 个阶段执行：
   - U2 `complete-step`
   - U1 `respond`
   - 重复到最后一个阶段
5. U2 提交 `resolved` 和解决方式。

断言：每一阶段 `curr_step_id`、`curr_step_agreed` 正确。

### 阶段四：U1 确认关闭

1. U1 查询详情，确认 `resolved`。
2. U1 调用状态接口提交 `closed`。

断言：`status=closed`，设置关闭时间。

## 6. 报告设计

Allure 第一层：

```text
Feature: 业务链路
Story: 摇人问答提单 -> AI派单 -> 处理人处理 -> 提单人关闭
Title: 真实后端：U1 提单，U2 处理并已解决，U1 关闭
```

每个步骤字段：

| 字段 | 示例 |
|---|---|
| 步骤号 | S17 |
| 角色 | U2 |
| 中文操作 | 确认接单开始处理 |
| 接口 | `POST /api/tasks/123/respond` |
| 业务状态 | `new -> in_progress` |
| 结果 | 通过 / 失败 |

失败时报告必须能区分：

- 环境访问失败
- 登录失败
- AI 固定回复未按预期推进
- 派单超时
- 业务状态不符合预期
- 清理失败

## 7. 安全边界

- 真实后端链路只允许 `push test` 和 `workflow_dispatch`。
- PR 只跑 Mock/无 secret 测试。
- 专用 SSH key 只允许转发 `127.0.0.1:9400`，不能执行远程命令。
- U1/U2 密码和 SSH 私钥只存在 GitHub Secrets。
- 日志、Allure 不输出密码、token、SSH 私钥。
- 测试工单必须带唯一标记，禁止在项目 `001` 创建数据。

## 8. 实施顺序

建议严格分三步，每步独立确认和验证：

### 第一步：会话与报告基础

- `automation/src/business_chain/session.py`
- `automation/src/business_chain/reporting.py`
- 对应单元测试

验证：本地 Mock 场景下可登录、切换角色、生成 Allure 步骤。

### 第二步：链路业务动作

- `automation/src/business_chain/sse.py`
- `automation/src/business_chain/call_ticket.py`
- `automation/tests/real/conftest.py`
- `automation/tests/real/test_call_to_ticket_close.py`

验证：在真实环境跑通完整链路或明确卡在可控 AI 回复。

### 第三步：CI 编排

- `.github/workflows/real-business-chain.yml`
- 报告发布和结果评论

验证：`push test` 或手动触发后，GitHub Actions 给出 Allure 报告入口。

## 9. 待确认问题

1. 固定问答和结构化草稿契约已确认；可控 AI 回复的具体实现方式仍待确认，未实现前无法稳定跑到 `draft_ready`。
2. 测试工单自动清理由哪个接口或脚本负责，权限归属是谁。
3. 测试账号的通知、统计和派单记录是否需要额外屏蔽。
4. Allure 报告最终使用 GitHub Pages 长期链接，还是本次 run 的 artifact 链接。
5. 是否允许在第一条链路稳定后，再扩展到异常分支和 AI 质量验证。

## 10. 依据

只读参考：

- `automation/docs/business-flows/call-qa-to-ticket-close.md`
- `automation/docs/business-flows/real-env-inventory-u1u2.md`
- `automation/docs/design-business-chain-refactor.md`
- `automation/tests/real/test_real_backend_smoke.py`
- `automation/tests/conftest.py`
- `automation/src/clients/api_client.py`
- `automation/config/models.py`
- `frontend/src/api/ai.ts`
- `frontend/src/api/conversation.ts`
- `frontend/src/api/ticket.ts`
- `backend/app/modules/tasks/api/task.py`
- `ai/api/router.py`

本文件仅定义设计，尚未创建或修改任何执行层代码。
