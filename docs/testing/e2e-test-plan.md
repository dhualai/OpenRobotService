# E2E 模块测试计划

## 概述

E2E 测试基于共享的 `MockBackend` 实例，模拟多角色协作场景。
核心设计：一个 backend 实例 + 三类角色 token（admin / engineer / customer），
在同一次测试中切换身份完成完整业务流程。

## 架构

```
e2e/
├── conftest.py       → e2e_ctx fixture
├── flows/
│   ├── ticket_to_task.py  → TicketFlow helper
│   └── wechat_qa.py       → WeChatQAFlow helper
└── tests/
    └── test_critical_paths.py  → 6 个 E2E 测试
```

## 关键路径

### 路径 1：工单全生命周期（2 用例）

```
管理员建单 → 工程师自分配 → 诊断（加评论）
  → 标记处理中 → 解决 → 管理员关闭
```

覆盖：创建 / 查询 / 更新 / 状态流转 / 评论 / 关闭

| 步骤 | 角色 | 动作 | 验证点 |
|------|------|------|--------|
| 1 | admin | POST /api/tasks | status == pending |
| 2 | engineer | PATCH .../assign | assigned_to != null |
| 3 | engineer | PATCH .../status → in_progress | status == in_progress |
| 4 | engineer | POST .../comments | 201 Created |
| 5 | engineer | PATCH .../status → resolved | status == resolved |
| 6 | admin | PATCH .../status → closed | status == closed, comments ≥ 2 |

### 路径 2：QA → 工单串联（2 用例）

```
客户咨询 → QA 回复 → 创建会话
  → 工程师跟进建工单
```

| 步骤 | 角色 | 动作 | 验证点 |
|------|------|------|--------|
| 1 | customer | POST /api/qa/ask | success == true |
| 2 | customer | POST /api/conversations | id > 0 |
| 3 | engineer | POST /api/tasks | status == pending |

### 路径 3：多角色协作（2 用例）

```
管理员建单 → 客户查看我的工单
  → 工程师接管 → AI 自动分配
```

| 步骤 | 角色 | 动作 | 验证点 |
|------|------|------|--------|
| 1 | admin | POST /api/tasks | 创建成功 |
| 2 | customer | GET /api/my-tasks/ | 200, 含 items |
| 3 | engineer | PATCH .../status | status == in_progress |
| 4 | admin | POST .../ai-assign | confidence > 0 |

## 设计原则

1. **共享 backend 实例**：一个 MockBackend 跨角色共享，不同身份通过不同 token 区分
2. **顺序无关**：每个测试独立建数据，不依赖其他测试副作用
3. **Flow helper 封装 API 通信**：测试代码不直接调 request，通过 TicketFlow / WeChatQAFlow 操作
4. **断言分层**：helper 验证 HTTP 状态码，测试验证业务逻辑

## 运行方式

```bash
pytest automation/e2e/tests/ -v
```

## 当前状态

**状态**：✅ 已实现并提交（dbdd29b）
**测试数**：6 用例（3 条关键路径）
**依赖**：无外部服务（基于 MockBackend）
**后续补充**（P3）：
- WeChat → 通知 → 建单
- Admin 筛选 → 统计 → 导出
- 删除 → 恢复路径
