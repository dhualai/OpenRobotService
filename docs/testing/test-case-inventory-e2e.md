# E2E 测试用例清单

> 格式按 [template-test-case.md](template-test-case.md)

---

## Ticket Lifecycle

### E2E-TC-001 — test_full_ticket_lifecycle

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 完整工单生命周期

**测试点：** 验证创建->分派->处理->诊断->解决->关闭全流程

**前置条件：** 三种角色 Token 可用

**测试步骤：**
1. admin 登录创建工单
2. admin 分派给 engineer
3. engineer 处理工单
4. AI 诊断
5. admin 解决工单
6. admin 关闭工单

**结果：** PASS

---

## Ticket Lifecycle

### E2E-TC-002 — test_invalid_status_transition_blocked

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 非法状态流转

**测试点：** 验证跳过合法状态直接流转返回 400

**前置条件：** 已创建工单状态为 pending

**测试步骤：**
1. 尝试 PATCH status=resolved（跳过 in_progress）→ 400

**结果：** PASS

---

## QA Integration

### E2E-TC-003 — test_qa_to_conversation_flow

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 QA 到会话流程

**测试点：** 验证 QA 提问后创建会话的端到端流程

**前置条件：** 已登录 customer

**测试步骤：**
1. customer 发起 QA 提问
2. 系统创建会话
3. 验证会话信息

**结果：** PASS

---

## QA Integration

### E2E-TC-004 — test_create_ticket_after_qa

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 QA 后创建工单

**测试点：** 验证 QA 后创建工单的流程

**前置条件：** 已完成 QA 提问

**测试步骤：**
1. 基于 QA 结果创建工单
2. 验证工单创建成功

**结果：** PASS

---

## Multi-Role Collaboration

### E2E-TC-005 — test_multi_role_collaboration

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 多角色协作

**测试点：** 验证管理员建单->客户查看->工程师处理->AI派单的多角色流程

**前置条件：** 三类角色 Token 可用

**测试步骤：**
1. admin 登录创建工单
2. customer 登录查看
3. engineer 登录处理
4. AI 自动派单

**结果：** PASS

---

## Multi-Role Collaboration

### E2E-TC-006 — test_ai_assign_after_ticket_creation

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 AI 派单

**测试点：** 验证建单后 AI 自动派单

**前置条件：** 已登录 admin；有待分配工单

**测试步骤：**
1. admin 创建工单
2. 触发 AI 派单
3. 验证派单结果

**结果：** PASS

---
