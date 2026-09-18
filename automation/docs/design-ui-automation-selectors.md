# UI 自动化定位器评估与改造清单（候选）

> 状态：只读评估稿，待确认
> 日期：2026-09-16
> 范围：U1 摇人问答提单 -> U2 处理 -> U1 关闭的 Playwright UI 自动化

## 1. 结论

项目适合做 Playwright UI 自动化，但目标页面目前缺少统一的
`data-testid`，如果全部依赖中文文案、placeholder 和 CSS class，长期维护成本会比较高。

推荐策略：

- Playwright 负责稳定执行。
- `data-testid` 负责关键交互元素。
- role / aria-label / 文本用于辅助定位和可访问性断言。
- API/DB 负责业务状态断言。
- AI 只辅助生成、修复和失败分析，不让 AI 自由决定点击路径。

## 2. 目标页面

| 页面 | 路由 | 主要文件 |
|---|---|---|
| 登录页 | `/login` | `frontend/src/pages/Login.tsx` |
| 我要摇人 | `/call` | `frontend/src/pages/call/CallView.tsx` |
| AI 对话面板 | `/call` | `frontend/src/shared/components/ChatPanel.tsx` |
| 摇人历史工单 | `/call/history` | `frontend/src/pages/call/HistoryTickets.tsx` |
| 工单详情 | `/tasks/{id}` 或 `/call/history` 详情入口 | `TicketDetailPage.tsx` / `TaskDetailPage.tsx` |
| 系统任务列表 | `/tasks` | `frontend/src/pages/tasks/TasksView.tsx` |

## 3. 现有定位条件

### 3.1 可以直接使用

| 位置 | 现有定位条件 | 稳定性 |
|---|---|---|
| 底部导航 | `data-testid="app-bottom-nav"`、`nav-item-{tab}` | 高 |
| 登录账号 | placeholder `请输入账号` | 中 |
| 登录密码 | placeholder `请输入密码` | 中 |
| 登录按钮 | 文本 `登录` | 中 |
| 摇人顶部菜单 | `aria-label="会话列表"` | 中高 |
| 摇人历史入口 | `aria-label="历史工单"` | 中高 |
| 对话输入框 | placeholder `发消息…` | 中 |
| 发送按钮 | `aria-label="发送"` | 中高 |
| 转工单按钮 | `aria-label="转工单"` / `"转为工单"` | 中，存在文案变体 |
| 工单状态/人员 | `aria-label` / 文本 | 中 |
| 详情页多个操作 | 文本 `确认同意`、`当前阶段完成`、`确认关闭` | 中低，文案可能调整 |

### 3.2 现在不适合作为主定位

- 纯 CSS class：`.chat-send-btn`、`.login-btn`、`.task-card2__*` 等。
- 纯中文文本：产品文案调整会导致测试失败。
- 动态流式 AI 内容：不能依赖具体回复全文。
- 数字 ID class 或动态列表顺序。

## 4. 推荐补充的 `data-testid`

### 4.1 登录页

| testid | 元素 |
|---|---|
| `login-username` | 账号输入框 |
| `login-password` | 密码输入框 |
| `login-show-password` | 显示/隐藏密码按钮 |
| `login-submit` | 账密登录按钮 |
| `login-wechat` | 微信登录按钮（若启用） |

### 4.2 我要摇人 / ChatPanel

| testid | 元素 |
|---|---|
| `call-conversation-menu` | 左侧会话列表按钮 |
| `call-history-entry` | 右上角历史工单 |
| `chat-message-list` | 消息列表容器 |
| `chat-input` | 对话输入框 |
| `chat-send` | 发送按钮 |
| `chat-transfer-ticket` | 转工单悬浮球 |
| `chat-ticket-draft-modal` | 转工单确认弹窗 |
| `chat-ticket-title` | 草稿标题 |
| `chat-ticket-description` | 草稿描述 |
| `chat-ticket-project` | 项目选择 |
| `chat-ticket-step` | 处理阶段选择 |
| `chat-ticket-confirm` | 确认提单按钮 |
| `chat-ticket-cancel` | 取消按钮 |
| `chat-assistant-processing` | AI 处理中状态 |
| `chat-ticket-created` | 工单创建成功气泡 |

### 4.3 工单列表和详情

| testid | 元素 |
|---|---|
| `tasks-search` | 工单搜索框 |
| `tasks-create` | 新建工单 |
| `task-card-{id}` | 工单列表项 |
| `task-status` | 当前状态 |
| `task-assignee` | 当前处理人 |
| `task-current-step` | 当前协商阶段 |
| `task-accept` | 确认接单 / 确认同意 |
| `task-complete-step` | 当前阶段完成 |
| `task-resolve` | 处理完成 / 最末阶段结束 |
| `task-resolution-summary` | 解决方式输入 |
| `task-close` | 确认关闭 |
| `task-step-modal` | 阶段选择弹窗 |
| `task-step-next` | 下一阶段选择 |
| `task-step-endtime` | 阶段结束时间 |
| `task-step-submit` | 阶段提交 |

`{id}` 使用真实工单 ID，例如 `task-card-837`，避免列表顺序定位。

### 4.4 状态与消息

| testid | 元素 |
|---|---|
| `toast-message` | 全局 Toast |
| `loading-indicator` | 全局加载态 |
| `empty-state` | 空状态 |
| `error-state` | 错误状态 |

## 5. 定位优先级

Playwright 定位建议按以下顺序：

1. `data-testid`
2. role + accessible name
3. label / placeholder
4. 稳定文本
5. CSS class

不建议把 CSS class 作为主要定位器，也不建议依赖列表中的第 N 个元素。

## 6. UI 自动化与 API 断言结合

UI 只负责验证用户真实操作路径，业务正确性仍由 API/DB 校验。

示例：

```text
UI：U1 输入问题 -> 点击发送 -> 等待草稿 -> 确认提单
API：GET /api/tasks/{id} 校验 created_by/status/project_id
UI：U2 打开详情 -> 确认接单 -> 推进阶段
API：校验 status/curr_step/curr_step_agreed
UI：U1 关闭工单
API：校验 status=closed/closed_at
```

## 7. 第一条 UI Smoke 链路

建议先只做：

```text
U1 登录
-> 进入“我要摇人”
-> 输入固定问题
-> 等待 AI 回复/草稿
-> 点击转工单
-> 确认提单
-> API 校验工单已创建
```

第一条稳定后，再增加：

```text
U2 登录
-> 待我处理
-> 接单
-> 推进 3 个阶段
-> 提交已解决
-> U1 确认关闭
```

## 8. 风险

- 流式 AI 回复不能靠固定文本或固定时长等待。
- 页面文案和弹窗结构变化会破坏文本定位，因此关键控件必须有 `data-testid`。
- 微信登录、H5 兼容、图片/附件场景不适合作为第一版 UI Smoke。
- 真实环境 UI 测试仍需要可控 AI 回复和测试数据清理。

## 9. 实施顺序

1. 前端单独 PR 增加 `data-testid`，不改变业务逻辑和样式。
2. automation 增加 Playwright UI fixtures 和 Page Object。
3. 实现 U1 提单 UI Smoke。
4. 增加 API/DB 断言和截图/trace。
5. 增加 U2 处理到关闭完整链路。
6. 接入 test 分支 workflow，PR 阶段只跑 Mock/无 secret UI。

