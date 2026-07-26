# 测试命名规范

> 本文定义测试文件、函数、类的命名规范。三模块统一执行。

---

## 一、文件名规范

### 1.1 后端

| 类型 | 格式 | 示例 |
|------|------|------|
| 通用测试 | `test_{模块名}.py` | `test_adapter.py` |
| 按功能划分 | `test_{功能}.py` | `test_map_status.py` |
| API 集成测试 | `test_{端点}_api.py` | `test_task_creation_api.py` |
| DB 集成测试 | `test_{功能}_db.py` | `test_task_creation_db.py` |
| 数据文件 | `sample_{说明}.json` / `sample_{说明}.py` | `sample_zentao_task.json` |

### 1.2 前端

| 类型 | 格式 | 示例 |
|------|------|------|
| 组件测试 | `{组件名}.test.tsx` | `MainLayout.test.tsx` |
| 纯逻辑测试 | `{文件名}.test.ts` | `auth.test.ts` |
| 工具测试 | `{工具名}.test.ts` | `url.test.ts` |

### 1.3 AI 模块

| 类型 | 格式 | 示例 |
|------|------|------|
| 正式 pytest 测试 | `test_{功能}.py` | `test_llm_api.py` |
| 交互诊断脚本 | `{用途描述}.py`（放 `scripts/` 下） | `agent_chat.py` |
| 基准测试 | `{用途}_benchmark.py`（放 `scripts/` 下） | `ttft_benchmark.py` |

---

## 二、函数/方法命名规范

### 2.1 后端

```python
# 通用格式
def test_{功能}__{场景}():
def test_{功能}_when_{条件}():

# 正常路径
def test_map_status_wait_to_new():
def test_full_sample_mapping():

# 边界值
def test_map_status_when_empty_string():
def test_map_status_when_none():
def test_map_status_when_unknown():

# 异常路径
def test_create_task_when_missing_required_field():
def test_fetch_tasks_when_api_unreachable():

# 参数化测试（用例描述在 parametrize 中）
@pytest.mark.parametrize("input,expected", [ ... ])
def test_map_status(input, expected):
```

#### 命名原则

| 原则 | 说明 |
|------|------|
| 蛇形命名 | 全小写 + 下划线分隔 |
| 描述行为 | 不描述实现，描述"做什么" |
| 含预期 | 复杂场景在函数名中体现预期 |
| 不含 and | 一个测试只验证一件事，不用 `_and_` |

### 2.2 前端

```typescript
// 通用格式
describe('{组件/函数名}', () => {
  it('{场景描述}', () => { ... });
  it('{期望行为} when {条件}', () => { ... });
});

// 正常路径
describe('Login', () => {
  it('renders login form', () => { ... });
  it('calls login API on submit', () => { ... });
});

// 异常路径
describe('Login', () => {
  it('shows error when API fails', () => { ... });
  it('disables button when submitting', () => { ... });
});

// Store 测试
describe('useAuthStore', () => {
  it('sets isLoggedIn on login', () => { ... });
  it('clears state on logout', () => { ... });
});
```

#### 命名原则

| 原则 | 说明 |
|------|------|
| describe 用组件/函数名 | 大写开头，与被测对象一致 |
| it 用自然语言 | 使用英文或中文(选择一种，保持一致) |
| 一个 it 一个断言 | 不在一句话中列多个期望 |

---

## 三、类命名规范

仅在需要按功能分组测试时使用类：

```python
# 后端
class TestZentaoMapper:
    """禅道映射器测试"""

    def test_map_status(self):
        ...

class TestSyncEngine:
    """同步引擎测试"""

    def test_merge_status(self):
        ...
```

```typescript
// 前端（不强制使用类，用 describe 替代）
describe('authService', () => { ... });
```

---

## 四、变量命名

```python
# 后端
expected_status = TaskStatus.NEW
mock_response = {"id": 123}
sample_task = SAMPLE_TASK_DATA
mock_adapter = MagicMock(spec=TaskSourceAdapter)
```

```typescript
// 前端
const mockUser = { username: 'test', role: 'engineer' };
const expectedUrl = '/api/tasks';
const onMock = vi.fn();
```

---

## 五、目录名规范

| 目录 | 后端 | 前端 | AI |
|------|------|------|-----|
| 测试根 | `tests/` | `**/__tests__/` | `tests/` |
| 子模块 | `tests/{模块名}/` | `{模块}/__tests__/` | `tests/{子模块}/` |
| 数据 | `tests/{模块名}/data/` | 不单独建目录 | `tests/data/` |
| 工具 | `tests/utils/` | N/A（用 setup.ts） | `tests/utils/` |
| 脚本 | N/A | N/A | `tests/scripts/` |

---

## 六、规范校验

```powershell
# 后端：检查 pytest 可发现所有测试
cd backend
pytest --collect-only --quiet

# 前端：检查 vitest 可发现所有测试
cd frontend
npx vitest --run --reporter=verbose
```
