# Task 34：Mock 完整业务链路 Demo

> 日期：2026-09-11
> 状态：已实现并通过验证

## 目标

在无真实环境、无可控 AI 回复的条件下，用 MockBackend 实现“U1 提问提单 -> 派单 U2 -> U2 处理 -> 已解决 -> U1 关闭”的完整业务链路，并生成 Allure 报告。

## 实现内容

1. MockBackend 新增 U1/U2 测试账号：
   - `u1_auto / 123456`
   - `u2_auto / 123456`
2. MockBackend 新增 AI 契约：
   - `/api/ai/qa/ask/stream`
   - `/api/ai/qa/ticket/prepare`
   - `/api/ai/qa/ticket/steps`
   - `/api/ai/qa/ticket/confirm`
3. MockBackend 新增任务处理接口：
   - `POST /api/tasks/{id}/respond`
   - `POST /api/tasks/{id}/complete-step`
4. 扩展任务状态机：支持 `new -> in_progress -> resolved -> closed`。
5. 新增完整业务链路测试：
   - `automation/tests/business_chain/test_call_to_ticket_close.py`
6. 生成 Allure 链路报告：
   - `automation/output/allure-report-chain/index.html`

## 修改文件

- `automation/src/mocks/backend_mock.py`
- `automation/tests/business_chain/__init__.py`
- `automation/tests/business_chain/test_call_to_ticket_close.py`
- `automation/docs/worklog/task-34-mock-business-chain-demo.md`

## 验证结果

```text
1 passed in 0.23s
```

完整 API 回归：

```text
256 passed, 3 skipped, 32 deselected
```

3 条跳过用例仍为真实环境 smoke 测试，当前使用 Mock 模式。

## 报告

- 本地 Allure：`http://127.0.0.1:8083`
- 报告文件：`automation/output/allure-report-chain/index.html`

## 风险

- 本 Demo 使用 MockBackend，只验证预期业务链路和测试框架，不代表真实后端产品逻辑已经验证。
- 真实后端链路上线仍依赖可控 AI 回复、测试工单清理和测试账号通知隔离。

## 下一步

1. 用 Allure 报告向团队确认 Demo 形态。
2. 真实环境可用后，将同一链路切换到真实后端执行。
3. 补充 CI 触发和报告归档。
