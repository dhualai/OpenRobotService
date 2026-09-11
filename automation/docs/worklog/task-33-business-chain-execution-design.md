# Task 33：业务链路执行层设计

> 日期：2026-09-11
> 状态：设计完成，待人工确认

## 目标

把已确认的“摇人问答 -> 提单 -> 派单 -> 处理 -> 关闭”链路规范落到现有 `pytest + ApiClient + Allure` 框架，进入实现前的设计评审。

## 阅读内容

- `automation/AGENTS.md`
- `.agents/skills/automation-testing/SKILL.md`
- `.claude/SKILLS/代码修改约束/AI代码修改边界Skill.md`
- `automation/docs/automation_strategy.md`
- `automation/docs/testing/testing_guidelines.md`
- `automation/docs/testing/test_report_guideline.md`
- `automation/docs/ci-automation-loop.md`
- `automation/docs/business-flows/call-qa-to-ticket-close.md`
- `automation/docs/business-flows/real-env-inventory-u1u2.md`
- `automation/tests/real/test_real_backend_smoke.py`
- `automation/tests/conftest.py`
- `automation/src/clients/api_client.py`
- `automation/config/models.py`
- `frontend/src/api/ai.ts`
- `frontend/src/api/conversation.ts`
- `frontend/src/api/ticket.ts`
- `backend/app/modules/tasks/api/task.py`
- `ai/api/router.py`

## 产出

- `automation/docs/design-business-chain-execution-layer.md`

设计包含：

- 执行层目录和文件划分
- U1/U2 会话模型
- 业务步骤和 Allure 报告模型
- SSE 流解析设计
- 真实后端测试文件设计
- CI workflow 触发和安全边界
- 三步实施顺序
- 待确认问题
- 已确认的固定问答和结构化草稿契约：
  - 项目固定为 `Leo_test`
  - 类型固定为 `problem`
  - 指定处理人使用显示名“自动化处理人”
  - 测试只断言结构化草稿字段，不断言 AI 回复全文

## 测试结果

- 本轮为设计阶段，未编写执行层代码，未运行新增测试。
- 已有 `Real Environment Access Check` 已通过，证明真实环境访问链路可用。

## 风险

- 可控 AI 回复实现方式未确认，是完整链路的阻塞点。
- 测试工单自动清理接口和权限未确认。
- 测试账号的业务通知和统计影响未确认。

## 下一步

1. 人工 review `design-business-chain-execution-layer.md`。
2. 确认可控 AI 回复的实现方式和清理方案。
3. 确认后按“会话与报告基础 -> 链路业务动作 -> CI 编排”三步实现。
