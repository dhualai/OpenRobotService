# call 模块 - 用例清单（代码驱动）

> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 41 条。

| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |
|------|--------|------|------|----------|------|
| test_ask_ai_timeout_fallback | 我要摇人 | 问答 | AI：AI 诊断超时降级 | AI | `-` |
| test_ask_empty_question | 我要摇人 | 问答 | 数据校验：空问题 | 数据校验 | `-` |
| test_ask_question | 我要摇人 | 问答 | 正常：提问 | 正常流程 | `-` |
| test_ask_stream | 我要摇人 | 问答 | AI：流式问答 | AI | `-` |
| test_assignable_users | 我要摇人 | 升级选人 | 正常：升级选人列表 | 正常流程 | `-` |
| test_assignable_users_available | 我要摇人 | 升级选人 | 正常：返回可选人员 | 正常流程 | `-` |
| test_assignable_users_empty | 我要摇人 | 升级选人 | 正常：无可用人员 | 正常流程 | `-` |
| test_assignable_users_invalid_project | 我要摇人 | 升级选人 | 数据校验：project_id 非法 | 数据校验 | `-` |
| test_create_conversation | 我要摇人 | 会话 | 正常：创建会话 | 正常流程 | `-` |
| test_cuiban | 我要摇人 | 催办 | 正常：催办 | 正常流程 | `-` |
| test_cuiban_long_note | 我要摇人 | 催办 | 数据校验：催办备注超长 | 数据校验 | `-` |
| test_cuiban_missing_task_id | 我要摇人 | 催办 | 数据校验：缺少 task_id | 数据校验 | `-` |
| test_cuiban_no_ratelimit | 我要摇人 | 催办 | 正常：催办（mock 暂不实现限流） | 正常流程 | `-` |
| test_cuiban_task_not_found | 我要摇人 | 催办 | 异常：催办任务不存在 | 异常流程 | `-` |
| test_delete_conversation | 我要摇人 | 会话 | 正常：删除会话 | 正常流程 | `-` |
| test_delete_conversation_not_found | 我要摇人 | 会话 | 异常：删除会话不存在 | 异常流程 | `-` |
| test_delete_message | 我要摇人 | 消息 | 正常：删除消息 | 正常流程 | `-` |
| test_delete_message_not_found | 我要摇人 | 消息 | 异常：删除消息不存在 | 异常流程 | `-` |
| test_full_flow_ask_submit_ack | 我要摇人 | 全链路 | 全链路：提问→转工单→确认 | 全链路 | `-` |
| test_get_conversation_detail | 我要摇人 | 会话 | 正常：会话详情 | 正常流程 | `-` |
| test_get_conversation_not_found | 我要摇人 | 会话 | 异常：会话不存在 | 异常流程 | `-` |
| test_get_message_detail | 我要摇人 | 消息 | 正常：消息详情 | 正常流程 | `-` |
| test_get_message_not_found | 我要摇人 | 消息 | 异常：消息不存在 | 异常流程 | `-` |
| test_list_conversations | 我要摇人 | 会话 | 正常：会话列表 | 正常流程 | `-` |
| test_list_messages | 我要摇人 | 消息 | 正常：消息列表 | 正常流程 | `-` |
| test_list_messages_missing_conv | 我要摇人 | 消息 | 数据校验：缺 conversation_id | 数据校验 | `-` |
| test_login_empty_password | 我要摇人 | 认证 | 数据校验：空 password | 数据校验 | `-` |
| test_login_wrong_password | 我要摇人 | 认证 | 异常：密码错误 | 异常流程 | `-` |
| test_me | 我要摇人 | 认证 | 正常：获取当前用户 | 正常流程 | `-` |
| test_me_invalid_token | 我要摇人 | 认证 | 权限：无效 token | 权限 | `-` |
| test_my_tasks_create | 我要摇人 | 我的任务 | 正常：创建我的任务 | 正常流程 | `-` |
| test_my_tasks_detail | 我要摇人 | 我的任务 | 正常：我的任务详情 | 正常流程 | `-` |
| test_my_tasks_list | 我要摇人 | 我的任务 | 正常：我的任务列表 | 正常流程 | `-` |
| test_send_message | 我要摇人 | 消息 | 正常：发送消息 | 正常流程 | `-` |
| test_submit_ticket | 我要摇人 | 转工单 | 正常：转工单（mock 暂不实现冲突检测） | 正常流程 | `-` |
| test_submit_ticket_unauthorized | 我要摇人 | 转工单 | 权限：未认证提交转工单 | 权限 | `-` |
| test_update_conversation | 我要摇人 | 会话 | 正常：更新会话 | 正常流程 | `-` |
| test_update_conversation_empty_title | 我要摇人 | 会话 | 数据校验：title 为空 | 数据校验 | `-` |
| test_update_conversation_not_found | 我要摇人 | 会话 | 异常：更新会话不存在 | 异常流程 | `-` |
| test_update_message | 我要摇人 | 消息 | 正常：更新消息 | 正常流程 | `-` |
| test_update_message_not_found | 我要摇人 | 消息 | 异常：更新消息不存在 | 异常流程 | `-` |
