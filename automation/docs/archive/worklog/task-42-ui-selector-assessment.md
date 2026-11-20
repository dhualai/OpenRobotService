# Task 42：UI 自动化定位器只读评估

> 日期：2026-09-16
> 状态：评估完成，待确认

## 目标

评估现有前端页面是否适合做 Playwright UI 自动化，并列出需要补充的
`data-testid`。

## 阅读范围

- `frontend/src/pages/Login.tsx`
- `frontend/src/pages/call/CallView.tsx`
- `frontend/src/shared/components/ChatPanel.tsx`
- `frontend/src/pages/call/TicketDetailPage.tsx`
- `frontend/src/pages/tasks/TasksView.tsx`
- `frontend/src/pages/tasks/TaskDetailPage.tsx`
- `frontend/src/shared/components/MainLayout.tsx`

## 结论

- 项目适合做 Playwright UI 自动化。
- 现有 `data-testid` 主要覆盖底部导航，核心业务页面几乎为空。
- 部分按钮已有 aria-label 或 placeholder，可以临时使用。
- 工单详情、阶段弹窗、解决/关闭按钮应补充 `data-testid`。
- 建议先做 U1 登录 -> 摇人问答 -> 提单 UI Smoke。

## 产出

- `automation/docs/design-ui-automation-selectors.md`

## 下一步

1. 由前端补充第一批 `data-testid`。
2. 自动化侧实现 Playwright UI Smoke。
3. 接入 API/DB 业务断言和 Allure 截图/trace。
