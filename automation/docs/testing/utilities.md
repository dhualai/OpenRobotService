# 公共测试工具规范

> 本文定义三模块共用和专用的测试工具函数集合及规范。

---

## 一、后端测试工具

### 1.1 全局工具（`backend/tests/utils/`）

```python
# backend/tests/utils/factories.py
"""测试数据工厂"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import uuid4


def make_external_task(**overrides):
    """构造 ExternalTask 实例"""
    from app.integrations.base import ExternalTask
    from app.models.task import TaskStatus, TaskPriority, TaskType

    defaults = {
        "external_id": str(uuid4()),
        "title": "默认任务标题",
        "description": "默认任务描述",
        "status": TaskStatus.NEW,
        "priority": TaskPriority.MEDIUM,
        "task_type": TaskType.OTHER,
        "assigned_account": None,
        "created_account": None,
        "created_at": datetime.now(),
        "deadline_at": None,
        "url": None,
        "extra": {},
    }
    defaults.update(overrides)
    return ExternalTask(**defaults)


def make_mock_task(**overrides):
    """构造 mock Task ORM 对象"""
    from unittest.mock import MagicMock

    task = MagicMock()
    task.id = overrides.get("id", 1)
    task.title = overrides.get("title", "默认工单")
    task.source = overrides.get("source", "manual")
    task.external_id = overrides.get("external_id", None)
    task.status = overrides.get("status", "new")
    task.priority = overrides.get("priority", "medium")
    return task
```

```python
# backend/tests/utils/assertions.py
"""自定义断言辅助"""

from typing import Optional


def assert_external_task_equal(actual, expected, check_fields: Optional[list] = None):
    """比较两个 ExternalTask 实例的指定字段"""
    fields = check_fields or ["external_id", "title", "status", "priority"]
    for field in fields:
        actual_val = getattr(actual, field)
        expected_val = getattr(expected, field)
        assert actual_val == expected_val, (
            f"字段 {field} 不匹配: {actual_val} != {expected_val}"
        )
```

### 1.2 工具目录规范

```
backend/tests/
├── utils/
│   ├── __init__.py
│   ├── factories.py           # 数据工厂
│   ├── assertions.py          # 自定义断言
│   └── helpers.py             # 通用辅助函数
```

---

## 二、前端测试工具

### 2.1 现有工具（`frontend/src/test/setup.ts`）

当前 `setup.ts` 已包含：
- `@testing-library/jest-dom/vitest` 匹配器注册
- `afterEach` 自动 cleanup
- `localStorage` mock
- `matchMedia` mock

### 2.2 推荐新增（`frontend/src/test/`）

```typescript
// frontend/src/test/factories.ts
import type { Ticket, User } from '@/shared/types';

let counter = 0;

export function createMockTicket(overrides?: Partial<Ticket>): Ticket {
  counter++;
  return {
    id: `mock-ticket-${counter}`,
    title: '默认工单',
    status: 'new',
    priority: 'medium',
    type: 'problem',
    description: '默认描述',
    createdAt: new Date().toISOString(),
    ...overrides,
  } as Ticket;
}

export function createMockUser(overrides?: Partial<User>): User {
  counter++;
  return {
    username: `test-user-${counter}`,
    role: 'engineer',
    token: 'mock-token',
    isAdmin: false,
    ...overrides,
  } as User;
}
```

```typescript
// frontend/src/test/mocks/api.ts
/** API Mock 辅助 */
import { vi } from 'vitest';

export function mockFetchSuccess(data: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ code: 200, data }),
  });
}

export function mockFetchError(status = 500, message = 'Internal Error') {
  return vi.fn().mockResolvedValue({
    ok: false,
    status,
    json: () => Promise.resolve({ detail: message }),
  });
}

export function mockFetchNetworkError() {
  return vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
}
```

### 2.3 工具目录

```
frontend/src/test/
├── setup.ts                   # ★ 全局配置（已有，勿移）
├── factories.ts               # 数据工厂
├── mocks/
│   ├── api.ts                 # API Mock 辅助
│   └── store.ts               # Store 状态 Helper
└── utils.ts                   # 通用测试工具
```

---

## 三、AI 模块测试工具

```python
# ai/tests/utils/llm_mock.py
"""LLM Mock 辅助"""

from unittest.mock import AsyncMock


class MockLLMClient:
    """Mock LLM 客户端，支持流式和非流式调用"""

    def __init__(self, responses=None):
        self.responses = responses or ["模拟回复"]
        self.call_count = 0

    async def chat(self, messages, stream=False):
        self.call_count += 1
        response = self.responses[self.call_count - 1]

        if stream:
            async def _stream():
                for token in response:
                    yield token
            return _stream()

        return response

    async def chat_stream(self, messages):
        self.call_count += 1
        response = self.responses[self.call_count - 1]
        async for token in response:
            yield token


@pytest.fixture
def mock_llm():
    return MockLLMClient()
```

---

## 四、工具函数规范

| 要求 | 说明 |
|------|------|
| **有类型注解** | 所有工具函数必须有完整类型签名 |
| **有文档字符串** | 说明函数用途、参数、返回值 |
| **不自带副作用** | 工具函数应是纯函数或可预期副作用的 |
| **可组合** | 小函数可组合使用，不重复造轮子 |
| **跨模块通用** | 跨测试文件复用的才放入 `utils/` |
| **单一测试文件内** | 仅在单个文件中使用的 helper 写在文件顶部 |

---

## 五、相关文档

| 文档 | 路径 |
|------|------|
| 测试数据规范 | `test-data.md` |
| Fixture 与 Mock 规范 | `fixture-and-mock.md` |
| 目录结构规范 | `directory-structure.md` |
| 测试总览 | `index.md` |
