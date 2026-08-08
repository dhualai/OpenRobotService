# 我要摇人模块 - 测试场景设计（实际用例清单）

> 本清单由 `automation/scripts/cli-gen-scenario-docs.py` 从 `automation/testdata/cases/api-test-cases.xlsx` 自动生成，Excel 用例变化后请重跑脚本。

## 覆盖统计

| 覆盖类型 | 用例数 | 用例 ID |
|----------|--------|---------|
| 正常流程 | 21 | CALL-001, CALL-002, CALL-003, CALL-005, CALL-008, CALL-009, CALL-010, CALL-013, CALL-015, CALL-016, CALL-017, CALL-020, CALL-024, CALL-025, CALL-026, CALL-029, CALL-031, CALL-033, CALL-035, CALL-037, CALL-039 |
| 异常流程 | 8 | CALL-004, CALL-012, CALL-018, CALL-027, CALL-030, CALL-034, CALL-036, CALL-038 |
| 权限 | 2 | CALL-021, CALL-040 |
| 数据校验 | 7 | CALL-006, CALL-011, CALL-014, CALL-019, CALL-022, CALL-028, CALL-032 |
| 全链路 | 1 | CALL-041 |
| AI | 2 | CALL-007, CALL-023 |

## 正常流程

| 用例ID | 接口 | 说明 |
|--------|------|------|
| CALL-001 | `POST /api/call/conversations -> 200` | 正常流程：创建会话 |
| CALL-002 | `GET /api/call/conversations -> 200` | 正常流程：会话列表 |
| CALL-003 | `GET /api/call/conversations/1 -> 200` | 正常流程：会话详情 |
| CALL-005 | `POST /api/call/qa/ask -> 200` | 正常流程：提问 |
| CALL-008 | `POST /api/call/messages -> 200` | 正常流程：发送消息 |
| CALL-009 | `GET /api/call/my-tasks/ -> 200` | 正常流程：我的任务列表 |
| CALL-010 | `POST /api/call/my-tasks/ -> 200` | 正常流程：创建我的任务 |
| CALL-013 | `GET /api/tasks/assignable-users -> 200` | 正常流程：返回可选人员 |
| CALL-015 | `POST /api/tasks/cuiban-notification -> 200` | 正常流程：催办 |
| CALL-016 | `GET /api/tasks/assignable-users -> 200` | 正常流程：无可用人员 |
| CALL-017 | `GET /api/tasks/assignable-users -> 200` | 正常流程：升级选人列表 |
| CALL-020 | `GET /api/auth/me -> 200` | 正常流程：获取当前用户 |
| CALL-024 | `POST /api/ai/qa/submit -> 200` | 正常流程：转工单（mock暂不实现冲突检测） |
| CALL-025 | `POST /api/tasks/cuiban-notification -> 200` | 正常流程：催办（mock暂不实现限流） |
| CALL-026 | `PUT /api/call/conversations/1 -> 200` | 正常流程：更新会话 |
| CALL-029 | `DELETE /api/call/conversations/1 -> 204` | 正常流程：删除会话 |
| CALL-031 | `GET /api/call/messages?conversation_id=1 -> 200` | 正常流程：消息列表 |
| CALL-033 | `GET /api/call/messages/1 -> 200` | 正常流程：消息详情 |
| CALL-035 | `PUT /api/call/messages/1 -> 200` | 正常流程：更新消息 |
| CALL-037 | `DELETE /api/call/messages/1 -> 204` | 正常流程：删除消息 |
| CALL-039 | `GET /api/call/my-tasks/1 -> 200` | 正常流程：我的任务详情 |

## 异常流程

| 用例ID | 接口 | 说明 |
|--------|------|------|
| CALL-004 | `GET /api/call/conversations/99999 -> 404` | 异常流程：会话不存在 |
| CALL-012 | `POST /api/tasks/cuiban-notification -> 404` | 异常流程：催办任务不存在 |
| CALL-018 | `POST /api/auth/login -> 401` | 异常流程：密码错误 |
| CALL-027 | `PUT /api/call/conversations/99999 -> 404` | 异常流程：会话不存在 |
| CALL-030 | `DELETE /api/call/conversations/99999 -> 404` | 异常流程：会话不存在 |
| CALL-034 | `GET /api/call/messages/99999 -> 404` | 异常流程：消息不存在 |
| CALL-036 | `PUT /api/call/messages/99999 -> 404` | 异常流程：消息不存在 |
| CALL-038 | `DELETE /api/call/messages/99999 -> 404` | 异常流程：消息不存在 |

## 权限

| 用例ID | 接口 | 说明 |
|--------|------|------|
| CALL-021 | `GET /api/auth/me -> 401` | 权限：无效token |
| CALL-040 | `POST /api/ai/qa/submit -> 401` | 权限：未认证提交转工单 |

## 数据校验

| 用例ID | 接口 | 说明 |
|--------|------|------|
| CALL-006 | `POST /api/call/qa/ask -> 422` | 数据校验：空问题 |
| CALL-011 | `POST /api/tasks/cuiban-notification -> 422` | 数据校验：缺少task_id |
| CALL-014 | `GET /api/tasks/assignable-users?project_id=abc -> 400` | 数据校验：project_id非法 |
| CALL-019 | `POST /api/auth/login -> 422` | 数据校验：空password |
| CALL-022 | `POST /api/tasks/cuiban-notification -> 200` | 数据校验：催办备注超长 |
| CALL-028 | `PUT /api/call/conversations/1 -> 422` | 数据校验：title 为空 |
| CALL-032 | `GET /api/call/messages -> 422` | 数据校验：缺 conversation_id |

## 全链路

| 用例ID | 接口 | 说明 |
|--------|------|------|
| CALL-041 | `steps 全链路（多步串联）` | 全链路：提问→转工单→确认 |

## AI

| 用例ID | 接口 | 说明 |
|--------|------|------|
| CALL-007 | `POST /api/call/qa/ask/stream -> 200` | AI：流式问答 |
| CALL-023 | `POST /api/call/qa/ask -> 200` | AI：AI诊断超时降级 |

## 汇总表

| 用例ID | 接口 | 覆盖类型 | 说明 |
|--------|------|---------|------|
| CALL-001 | `POST /api/call/conversations -> 200` | 正常流程 | 正常流程：创建会话 |
| CALL-002 | `GET /api/call/conversations -> 200` | 正常流程 | 正常流程：会话列表 |
| CALL-003 | `GET /api/call/conversations/1 -> 200` | 正常流程 | 正常流程：会话详情 |
| CALL-004 | `GET /api/call/conversations/99999 -> 404` | 异常流程 | 异常流程：会话不存在 |
| CALL-005 | `POST /api/call/qa/ask -> 200` | 正常流程 | 正常流程：提问 |
| CALL-006 | `POST /api/call/qa/ask -> 422` | 数据校验 | 数据校验：空问题 |
| CALL-007 | `POST /api/call/qa/ask/stream -> 200` | AI | AI：流式问答 |
| CALL-008 | `POST /api/call/messages -> 200` | 正常流程 | 正常流程：发送消息 |
| CALL-009 | `GET /api/call/my-tasks/ -> 200` | 正常流程 | 正常流程：我的任务列表 |
| CALL-010 | `POST /api/call/my-tasks/ -> 200` | 正常流程 | 正常流程：创建我的任务 |
| CALL-011 | `POST /api/tasks/cuiban-notification -> 422` | 数据校验 | 数据校验：缺少task_id |
| CALL-012 | `POST /api/tasks/cuiban-notification -> 404` | 异常流程 | 异常流程：催办任务不存在 |
| CALL-013 | `GET /api/tasks/assignable-users -> 200` | 正常流程 | 正常流程：返回可选人员 |
| CALL-014 | `GET /api/tasks/assignable-users?project_id=abc -> 400` | 数据校验 | 数据校验：project_id非法 |
| CALL-015 | `POST /api/tasks/cuiban-notification -> 200` | 正常流程 | 正常流程：催办 |
| CALL-016 | `GET /api/tasks/assignable-users -> 200` | 正常流程 | 正常流程：无可用人员 |
| CALL-017 | `GET /api/tasks/assignable-users -> 200` | 正常流程 | 正常流程：升级选人列表 |
| CALL-018 | `POST /api/auth/login -> 401` | 异常流程 | 异常流程：密码错误 |
| CALL-019 | `POST /api/auth/login -> 422` | 数据校验 | 数据校验：空password |
| CALL-020 | `GET /api/auth/me -> 200` | 正常流程 | 正常流程：获取当前用户 |
| CALL-021 | `GET /api/auth/me -> 401` | 权限 | 权限：无效token |
| CALL-022 | `POST /api/tasks/cuiban-notification -> 200` | 数据校验 | 数据校验：催办备注超长 |
| CALL-023 | `POST /api/call/qa/ask -> 200` | AI | AI：AI诊断超时降级 |
| CALL-024 | `POST /api/ai/qa/submit -> 200` | 正常流程 | 正常流程：转工单（mock暂不实现冲突检测） |
| CALL-025 | `POST /api/tasks/cuiban-notification -> 200` | 正常流程 | 正常流程：催办（mock暂不实现限流） |
| CALL-026 | `PUT /api/call/conversations/1 -> 200` | 正常流程 | 正常流程：更新会话 |
| CALL-027 | `PUT /api/call/conversations/99999 -> 404` | 异常流程 | 异常流程：会话不存在 |
| CALL-028 | `PUT /api/call/conversations/1 -> 422` | 数据校验 | 数据校验：title 为空 |
| CALL-029 | `DELETE /api/call/conversations/1 -> 204` | 正常流程 | 正常流程：删除会话 |
| CALL-030 | `DELETE /api/call/conversations/99999 -> 404` | 异常流程 | 异常流程：会话不存在 |
| CALL-031 | `GET /api/call/messages?conversation_id=1 -> 200` | 正常流程 | 正常流程：消息列表 |
| CALL-032 | `GET /api/call/messages -> 422` | 数据校验 | 数据校验：缺 conversation_id |
| CALL-033 | `GET /api/call/messages/1 -> 200` | 正常流程 | 正常流程：消息详情 |
| CALL-034 | `GET /api/call/messages/99999 -> 404` | 异常流程 | 异常流程：消息不存在 |
| CALL-035 | `PUT /api/call/messages/1 -> 200` | 正常流程 | 正常流程：更新消息 |
| CALL-036 | `PUT /api/call/messages/99999 -> 404` | 异常流程 | 异常流程：消息不存在 |
| CALL-037 | `DELETE /api/call/messages/1 -> 204` | 正常流程 | 正常流程：删除消息 |
| CALL-038 | `DELETE /api/call/messages/99999 -> 404` | 异常流程 | 异常流程：消息不存在 |
| CALL-039 | `GET /api/call/my-tasks/1 -> 200` | 正常流程 | 正常流程：我的任务详情 |
| CALL-040 | `POST /api/ai/qa/submit -> 401` | 权限 | 权限：未认证提交转工单 |
| CALL-041 | `steps 全链路（多步串联）` | 全链路 | 全链路：提问→转工单→确认 |
