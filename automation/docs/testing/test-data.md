# 测试数据规范

> 本文定义测试数据的管理规范，包含数据来源、格式和复用方式。

---

## 一、数据来源分层

```
Inline 数据（优先）──> Python 常量文件 ──> JSON/YAML 文件 ──> Factory 函数
    │                      │                    │                   │
    └── 简单场景           └── 常量复用         └── 复杂结构       └── 动态生成
```

### 优先级规则

1. **Inline 数据**：测试函数内直接定义，适用于 3-5 行的小数据
2. **Python 常量**：测试文件或 `data/` 目录下，适用于跨函数复用的数据
3. **JSON/YAML 文件**：`data/` 目录下，适用于真实 API 响应等复杂结构
4. **Factory 函数**：适用于需要动态生成的测试数据

---

## 二、后端测试数据

### 2.1 Inline 数据

适用于参数化测试的小数据：

```python
@pytest.mark.parametrize("zentao_status,expected", [
    ("wait", TaskStatus.NEW),
    ("doing", TaskStatus.IN_PROGRESS),
    ("pause", TaskStatus.PENDING),
    ("done", TaskStatus.RESOLVED),
    ("closed", TaskStatus.CLOSED),
    ("", TaskStatus.NEW),
    (None, TaskStatus.NEW),
])
def test_map_status(zentao_status, expected):
    assert map_status(zentao_status) == expected
```

### 2.2 Python 常量

适用于跨测试复用的复杂数据：

```python
# tests/integrations/data/sample_zentao.py
"""禅道 API 响应样本数据"""

SAMPLE_ZENTAO_TASK = {
    "id": 123,
    "name": "【测试】验证派单流程",
    "desc": "功能描述：验证工单系统能否成功接收禅道同步的测试任务",
    "status": "wait",
    "pri": 1,
    "type": "devel",
    "assignedTo": {"account": "zhangjunlei", "realname": "张俊磊"},
    "openedBy": {"account": "zhangsan", "realname": "张三"},
    "openedDate": "2026-07-14T10:00:00Z",
    "deadline": "2026-07-20",
    "estimate": 8,
    "consumed": 3.5,
    "left": 4.5,
    "project": 42,
    "execution": 7,
}

SAMPLE_ZENTAO_TASK_WITH_EMPTY_DESC = {
    "id": 456,
    "name": "无描述任务",
    "desc": "",
    "status": "doing",
    "pri": 3,
    "type": "test",
    "assignedTo": "zhangjunlei",  # 字符串格式（非对象）
    "openedBy": {"account": "lisi", "realname": "李四"},
    "openedDate": "2026-07-15",
    "deadline": None,
}
```

### 2.3 Factory 函数

适用于需要动态生成数据的场景：

```python
# tests/utils/factories.py
"""测试数据工厂"""

def make_external_task(**overrides) -> ExternalTask:
    """构造 ExternalTask 实例"""
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
```

### 2.4 数据目录规范

```
tests/{子模块}/
├── __init__.py
├── data/
│   ├── __init__.py
│   ├── sample_{源名称}.py          # 按来源命名
│   └── sample_{源名称}.json        # 复杂结构用 JSON
├── test_{功能}.py
└── conftest.py
```

---

## 三、前端测试数据

### 3.1 Inline 数据

```typescript
// 简单测试数据直接定义
const mockTicket = {
  id: '1',
  title: '测试工单',
  status: 'new',
  priority: 'high',
};
```

### 3.2 常量文件

```typescript
// frontend/src/shared/constants/__tests__/ticket.test.ts
// 使用实际常量而非重复定义
import { TASK_STATUS_MAP, PRIORITY_MAP } from '@/shared/constants/ticket';
```

### 3.3 Factory 模式

```typescript
// frontend/src/test/factories.ts
export function createMockTicket(overrides?: Partial<Ticket>): Ticket {
  return {
    id: 'mock-id',
    title: '默认工单标题',
    status: 'new',
    priority: 'medium',
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}

export function createMockUser(overrides?: Partial<User>): User {
  return {
    username: 'test-user',
    role: 'engineer',
    token: 'mock-token',
    ...overrides,
  };
}
```

---

## 四、AI 模块测试数据

```python
# ai/tests/data/sample_queries.py
"""AI 模块测试查询样本"""

SAMPLE_FAQ_QUERIES = [
    {"query": "机器人报错代码E001", "expected_intent": "troubleshoot"},
    {"query": "如何开机", "expected_intent": "howto"},
    {"query": "你好", "expected_intent": "chat"},
]

SAMPLE_KB_DOCUMENTS = [
    {"title": "开机步骤", "content": "1. 接通电源 2. 按下开机键 3. 等待系统自检"},
    {"title": "E001 故障码", "content": "E001 表示电机过载，请检查负载是否超重"},
]
```

---

## 五、数据复用规则

| 层级 | 存放位置 | 复用范围 | 示例 |
|------|----------|----------|------|
| 测试函数内 | 函数体内 | 当前测试 | `zentao_status = "wait"` |
| 测试文件内 | 文件顶部常量 | 当前文件 | `SAMPLE_TASK = {...}` |
| 模块内 | `tests/{模块}/data/` | 当前模块 | `from .data.sample_zentao import SAMPLE_TASK` |
| 全局 | `tests/utils/factories.py` | 全部测试 | `from tests.utils.factories import make_task` |

---

## 六、数据质量要求

| 要求 | 说明 |
|------|------|
| **真实反映生产数据** | 测试数据应基于真实 API 响应或业务场景 |
| **覆盖边界值** | 空值、None、非法值、超长字符串、特殊字符 |
| **不含敏感信息** | 不使用真实密码、Token、API Key |
| **自描述** | 变量名/字段名清晰表明业务含义 |
| **不过度** | 只包含被测逻辑需要的最小字段集 |
