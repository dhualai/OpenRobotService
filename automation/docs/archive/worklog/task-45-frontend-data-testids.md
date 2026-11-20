# Task 45：前端 UI 回归关键定位器

> 日期：2026-09-19
> 状态：阶段 3 实现完成

## 目标

为“U1 提单 -> U2 处理 -> U1 关闭”UI 回归补充稳定的 `data-testid`，
避免依赖中文文案、placeholder 和 CSS class。

## 修改文件

- `frontend/src/pages/Login.tsx`
- `frontend/src/shared/components/ChatPanel.tsx`
- `frontend/src/shared/components/StepNegotiationCard.tsx`
- `frontend/src/pages/tasks/TasksView.tsx`
- `frontend/src/pages/tasks/TaskDetailPage.tsx`

## 新增定位器

登录页：

- `login-username`
- `login-password`
- `login-submit`

摇人问答和工单弹窗：

- `chat-new-conversation`
- `chat-input`
- `chat-send`
- `chat-transfer-ticket`
- `chat-ticket-draft-modal`
- `chat-ticket-title`
- `chat-ticket-description`
- `chat-ticket-confirm`
- `chat-ticket-overview-{db_id}`

系统任务列表：

- `tasks-search`
- `task-card-{id}`

工单详情：

- `task-status`
- `task-assignee`
- `task-close`

协商阶段：

- `task-current-step`
- `task-accept`
- `task-complete-step`
- `task-resolve`
- `task-step-next`
- `task-step-endtime`
- `task-step-submit`

解决方式：

- `task-resolution-summary`
- `task-resolve-confirm`

## 验证结果

前端正式构建：

```text
tsc -b && vite build
✓ built in 3m 44s
```

前端全量单测：

```text
Test Files  4 failed | 16 passed (20)
Tests       10 failed | 172 passed (182)
```

失败位于既有测试 mock 和 AdminLayout 测试：

- `@/stores/auth` mock 缺少 `isManualLogout`。
- AdminLayout 测试依赖已不存在的 `btn-text-small`。
- ProjectDetail 测试存在 `vi.mock` 提升变量错误。

这些失败与本次新增 `data-testid` 无关。

## 风险

- 前端全量测试当前不是全绿，后续 UI 场景实现时需要区分既有失败和本次回归失败。
- `task-resolve` 可能出现在顶部操作按钮或协商卡最末阶段按钮，但不会在同一状态下同时出现。
- 本次只增加定位属性，没有改变业务逻辑、样式和页面文案。

## 下一步

1. 获取阶段 3 提交使用的 ORS 工单号。
2. 提交并推送阶段 3。
3. 进入阶段 4，实现 Playwright 场景执行、网络捕获和 Allure 报告。
