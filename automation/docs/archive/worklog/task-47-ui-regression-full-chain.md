# Task 47：完整 UI 业务链路回归

> 日期：2026-09-19
> 状态：阶段 4B 实现完成，完整链路连续两次通过

## 目标

在本地正式前端产物上，使用两个独立浏览器会话执行一条真实测试环境业务链路：

```text
U1 提问提单
-> 自动派单 U2
-> U2 接单并推进处理阶段
-> U2 提交已解决
-> U1 确认关闭
```

场景只连接测试环境，不连接生产环境；接口顺序、页面截图和脱敏数据写入 Allure。

## 阅读内容

- `automation/AGENTS.md`
- `automation/docs/automation_strategy.md`
- `automation/docs/testing/testing_guidelines.md`
- `automation/docs/testing/test_report_guideline.md`
- `automation/docs/testing/analysis/analysis-ui-regression-business-chain.md`
- `automation/docs/business-flows/call-qa-to-ticket-close.md`
- `automation/docs/design-ui-regression-business-chain.md`
- `automation/docs/worklog/task-44-ui-regression-gateway.md`
- `automation/docs/worklog/task-45-frontend-data-testids.md`
- `automation/docs/worklog/task-46-ui-regression-execution-foundation.md`
- `frontend/src/shared/components/ChatPanel.tsx`
- `frontend/src/shared/components/StepNegotiationCard.tsx`
- `frontend/src/pages/tasks/TaskDetailPage.tsx`
- `frontend/src/pages/tasks/TasksView.tsx`

## 修改文件

- `automation/tests/ui/conftest.py`
- `automation/tests/ui/test_call_qa_to_ticket_close_regression.py`
- `automation/tests/conftest.py`
- `automation/src/ui_regression/capture.py`
- `automation/src/ui_regression/tests/test_capture.py`
- `automation/src/ui_regression/page_actions.py`
- `automation/src/logger/handlers.py`
- `automation/src/logger/tests/test_logger.py`
- `frontend/src/pages/tasks/TaskDetailPage.tsx`
- `frontend/src/shared/components/StepNegotiationCard.tsx`

## 实现内容

1. 新增 U1/U2 双 Browser Context 场景夹具：
   - 启动本地 Gateway。
   - 建立测试后端和 API-only 自动化 AI 两条 SSH 隧道。
   - 校验前端正式产物、账号和运行开关。
2. 新增完整 UI 场景：
   - U1 密码登录、新建会话、发送固定问题、生成并提交工单。
   - U2 登录、搜索并打开本次工单、确认接单。
   - U2 依次完成初步诊断和临时解决阶段。
   - U1 分别确认临时解决和最终解决阶段。
   - U2 填写解决方式并提交已解决。
   - U1 确认关闭。
3. 页面操作适配真实前端控件：
   - TDesign 按钮使用隐藏标记定位真实同级按钮。
   - 下一阶段按选项文字包含关系匹配，再读取真实 value。
   - Ant Design 日期控件通过真实日历选择和确认。
   - 跨角色同步改为重新进入系统任务、搜索并打开工单，避免无 WebSocket 时刷新卡在加载态。
4. 状态断言读取 `data-status`，不依赖中文状态文案。
5. 浏览器捕获的 URL 查询参数增加脱敏，`token`、`access_token` 等替换为 `[REDACTED]`。
6. UI 回归期间临时关闭 `httpx/httpcore` INFO 日志，避免代理访问日志把 JWT 带入 pytest 日志附件。
7. 登录步骤等待“登录成功”Toast 消失后再结束，避免 S02-S05、S08-S09 截图残留登录浮层。
8. `AllureLogHandler` 改为线程安全的全局缓冲，修复 Gateway 后台线程写日志导致 pytest 退出码非 0 的问题。
9. Allure 步骤标题补齐业务状态变化，每个实际接口作为子步骤展示：
   - `POST /api/tasks/{id}/respond -> HTTP 200`
   - `PATCH /api/tasks/{id}/status -> HTTP 200`
10. Allure 分类为“场景用例 / UI完整业务链路”。
11. 清理失败只写入告警，不覆盖业务链路结果。

## 验证结果

前端正式构建：

```text
vite build
8618 modules transformed
built in 47.53s
```

UI 回归基础单测：

```text
13 passed in 8.28s
```

日志处理器单测：

```text
17 passed in 0.23s
```

完整 UI 业务链路连续运行两次：

```text
1 passed in 25.29s
1 passed in 27.65s
```

核心接口均返回 200：

```text
POST /api/ai/qa/ticket/confirm
POST /api/tasks/{id}/respond
POST /api/tasks/{id}/complete-step
PATCH /api/tasks/{id}/status
```

最终状态：

```text
new -> in_progress -> resolved -> closed
```

## Allure 报告

已生成：

`automation/output/allure-report-ui-regression-4b/index.html`

报告核对：

- 顶层分类：`场景用例 / UI完整业务链路`
- 用例标题：`测试环境：U1提单，U2处理并已解决，U1关闭`
- 业务步骤：16 个，全部通过
- 业务状态：步骤标题直接显示 `new -> in_progress`、`in_progress -> resolved`、`resolved -> closed`
- 接口步骤：每个业务步骤下显示实际 `METHOD /path -> HTTP status`
- 每步附件：实际网络 JSON 和页面截图
- 敏感字段：Authorization、token、password 等已脱敏
- 安全扫描：最终结果和报告目录中检索不到 JWT 或原始 `token=` 值
- 截图核对：S02、S03、S08 不再显示上一步的“登录成功”Toast

## 风险

- 本地 Gateway 未代理 WebSocket，跨角色状态同步采用重新进入列表并打开工单，不依赖实时推送。
- 管理员 `DELETE /api/tasks/{id}` 在测试环境仍返回 500，失败运行会残留测试工单；本次多次调试产生了 `812` 至 `830`，会话已清理。
- 自动化 AI `9411` 依赖测试服务器用户级 `systemd` 服务，服务不可用时场景应判定为环境失败。
- 前端阶段日期控件与按钮封装已按当前 TDesign/Ant Design 版本适配，升级依赖后需要回归。
- 登录步骤会额外等待约 2-3 秒，用于确保 Toast 消失和报告截图干净。

## 下一步

1. 获取本次阶段 4B 提交使用的 ORS 工单号。
2. 提交并推送当前功能分支。
3. 实现“场景用例 + 单接口 Smoke”联合 Allure 样板报告。
4. 实现本地一键运行脚本和操作文档。
