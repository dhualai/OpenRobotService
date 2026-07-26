# E2E 测试用例清单

> 3 条关键路径，6 个测试用例。详情见 [e2e-test-plan.md](e2e-test-plan.md)。

| # | 测试函数 | 流程 | 涉及模块 |
|---|---------|------|---------|
| 1 | test_full_ticket_lifecycle | 创建→分派→处理→解决→关闭 | Auth + Tasks |
| 2 | test_invalid_status_transition_blocked | 跳过状态非法流转→400 | Tasks |
| 3 | test_qa_to_conversation_flow | QA 提问→创建会话 | QA + Conversation |
| 4 | test_create_ticket_after_qa | QA 后创建工单 | QA + Tasks |
| 5 | test_multi_role_collaboration | 管理员建单→客户→工程师 | Auth+Tasks+MyTasks |
| 6 | test_ai_assign_after_ticket_creation | 建单后 AI 派单 | Tasks |

**合计：6 用例 ✅**
