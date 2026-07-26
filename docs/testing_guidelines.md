# 自动化测试开发规范

> 本文定义 OpenRobotService 项目中自动化测试的编写规范、命名约定和最佳实践。
> 测试策略与分层见 `docs/automation_strategy.md`，报告规范见 `docs/test_report_guideline.md`。

---

## 一、测试框架

### 1.1 后端（Python）

| 项目 | 规格 |
|------|------|
| 框架 | pytest 9.1+ |
| 插件 | pytest-asyncio（异步测试）、httpx（API 测试）、allure-pytest（报告） |
| 运行器 | `pytest` |
| 配置文件 | `backend/pytest.ini`（可选） |
| 测试发现 | pytest 自动发现 `tests/` 下 `test_*.py` 或 `*_test.py` |

### 1.2 前端（TypeScript）

| 项目 | 规格 |
|------|------|
| 框架 | Vitest 3.2+ |
| 库 | @testing-library/react 16、@testing-library/jest-dom 6 |
| 环境 | jsdom 26（浏览器模拟） |
| 运行器 | `vitest`（通过 npm scripts） |
| 配置文件 | `frontend/vite.config.ts`（`test` 块内联） |
| 测试发现 | `**/__tests__/**` 目录下 `*.test.{ts,tsx}` |

---

## 二、命名规范

### 2.1 后端

```
tests/
├── __init__.py                          # 空文件，标记为包
├── conftest.py                          # 全局 pytest 夹具
├── test_{模块名}.py                     # 通用测试
├── {子模块}/
│   ├── __init__.py
│   ├── test_{功能}.py                   # 按功能划分的测试
│   └── test_{功能}_{场景}.py            # 按场景划分的测试
```

测试函数命名：
```python
# 通用格式
def test_{功能}_{场景}():
def test_{方法名}_when_{条件}():

# 示例
def test_parse_project_ids_when_comma_separated():
def test_is_enabled_false_when_any_missing():
def test_merge_status_when_incoming_advanced():
```

### 2.2 前端

```
src/{模块}/
└── __tests__/
    ├── {组件名}.test.tsx                # 组件测试
    └── {模块名}.test.ts                 # 纯逻辑测试
```

测试函数命名：
```typescript
// 通用格式
describe('{组件/函数名}', () => {
  it('{场景描述}', () => {
    // 期望行为
  });
  it('{异常场景描述}', () => {
    // 错误处理
  });
});
```

---

## 三、测试编写规范

### 3.1 后端测试

```python
# tests/integrations/test_mapper.py（参考示例）
import pytest
from app.integrations.sources.zentao.mapper import (
    zentao_task_to_external,
    map_status,
    map_priority,
    map_task_type,
)

# 参数化测试（覆盖多组输入输出）
@pytest.mark.parametrize("zentao_status,expected", [
    ("wait", TaskStatus.NEW),
    ("doing", TaskStatus.IN_PROGRESS),
    ("done", TaskStatus.RESOLVED),
    ("closed", TaskStatus.CLOSED),
    ("", TaskStatus.NEW),
    (None, TaskStatus.NEW),
    ("unknown", TaskStatus.NEW),
])
def test_map_status(zentao_status, expected):
    assert map_status(zentao_status) == expected

# 使用 conftest 的 mock 机制（无需真实 DB）
def test_full_sample_mapping():
    sample = {
        "id": 123,
        "name": "测试任务",
        "status": "wait",
        # ...
    }
    result = zentao_task_to_external(sample)
    assert result.title == "测试任务"
    assert result.status == TaskStatus.NEW
```

### 3.2 前端测试

```typescript
// frontend/src/stores/__tests__/auth.test.ts（参考示例）
import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from '../auth';

describe('useAuthStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ isLoggedIn: false, username: null });
  });

  it('should login successfully', () => {
    const store = useAuthStore.getState();
    store.login({ username: 'test', token: 'xxx' });
    expect(useAuthStore.getState().isLoggedIn).toBe(true);
  });

  it('should clear state on logout', () => {
    const store = useAuthStore.getState();
    store.login({ username: 'test', token: 'xxx' });
    store.logout();
    expect(useAuthStore.getState().isLoggedIn).toBe(false);
  });
});
```

### 3.3 测试标记（pytest markers）

```python
@pytest.mark.asyncio          # 异步测试
@pytest.mark.skipif(...)      # 条件跳过
@pytest.mark.parametrize(...) # 参数化
@pytest.mark.slow              # 慢速测试（定义在 pytest.ini）
```

---

## 四、断言规范

### 4.1 后端

```python
# 推荐
assert result.status == TaskStatus.NEW
assert result is None
assert len(items) == 5

# 使用内置断言，避免 self.assertEqual 等 unittest 风格
# 复杂比较用 pytest.approx
assert actual_value == pytest.approx(expected_value, rel=1e-3)
```

### 4.2 前端

```typescript
// 推荐
expect(screen.getByText('登录')).toBeInTheDocument();
expect(container.querySelector('.error')).toBeNull();
expect(onClickMock).toHaveBeenCalledTimes(1);

// Testing Library 优先使用角色查询
expect(screen.getByRole('button', { name: /提交/i })).toBeDisabled();
```

---

## 五、夹具（Fixtures）

### 5.1 后端 conftest.py

全局夹具已定义在 `backend/tests/conftest.py`：
- 占位模块 mock（`app`/`app.models`/`app.core`）
- 存根 `get_async_db` 替代数据库

新增夹具应放在：
- **全模块通用**：`backend/tests/conftest.py`
- **子模块专用**：`backend/tests/{子模块}/conftest.py`

### 5.2 前端 setup.ts

全局配置已定义在 `frontend/src/test/setup.ts`：
- `@testing-library/jest-dom/vitest` 匹配器注册
- `afterEach` 清理（`cleanup`）
- `localStorage` mock
- `matchMedia` mock

---

## 六、覆盖率要求

### 6.1 后端

| 层级 | 建议覆盖率 |
|------|------------|
| 映射函数（mapper） | ≥ 90% |
| 业务服务（services） | ≥ 80% |
| API 路由（routes） | ≥ 70% |
| 集成层（integrations） | ≥ 80% |

### 6.2 前端

| 层级 | 建议覆盖率 |
|------|------------|
| 纯逻辑（utils/constants/stores） | ≥ 85% |
| 组件（components） | ≥ 70% |
| 页面（pages） | ≥ 60% |

前端覆盖率由 `@vitest/coverage-v8` 生成，运行 `npm run test:coverage`。

---

## 七、相关文档

| 文档 | 路径 |
|------|------|
| 自动化测试方案 | `docs/automation_strategy.md` |
| 测试报告规范 | `docs/test_report_guideline.md` |
| Code Review 检查清单 | `docs/review_checklist.md` |
| 常见问题排查 | `docs/troubleshooting.md` |
| 项目架构说明 | `docs/project_architecture.md` |
| 业务规则 | `docs/business_rules.md` |
