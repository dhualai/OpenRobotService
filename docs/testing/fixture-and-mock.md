# Fixture 与 Mock 规范

> 本文定义测试中 Fixture 和 Mock 的使用规范。

---

## 一、后端 Fixture 规范

### 1.1 现有全局 Fixture（`backend/tests/conftest.py`）

当前 conftest.py 实现了**模块替换策略**来阻止数据库连接：

```python
# 作用：阻止 app/__init__.py 在 import 时触发 Base.metadata.create_all()
for _name, _sub in (("app", None), ("app.models", "models"), ("app.core", "core")):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        _m.__path__ = [os.path.join(_APP, _sub)] if _sub else [_APP]
        sys.modules[_name] = _m

# 作用：阻止 app.core.database 在 import 时触发真实的 get_async_db
async def _get_async_db():
    yield None
_database.get_async_db = _get_async_db
sys.modules["app.core.database"] = _database
```

**扩展规则**：当新增测试需要更多 Mock 时，按以下优先级进行：

| 优先级 | 方案 | 适用场景 |
|--------|------|----------|
| 1 | conftest.py 添加模块占位 | 阻止导入时副作用（DB 连接、初始化） |
| 2 | `unittest.mock.patch` | 单函数/方法级 Mock |
| 3 | `pytest.fixture` + `monkeypatch` | 模块级 Mock，需跨测试复用 |
| 4 | 自定义 fixture 函数 | 构造复杂测试对象 |

### 1.2 Fixture 作用域

| 作用域 | 声明 | 适用场景 |
|--------|------|----------|
| 函数级 | `@pytest.fixture` | 默认，每次测试独立 |
| 类级 | `@pytest.fixture(scope="class")` | 类内测试共享（谨慎使用） |
| 模块级 | `@pytest.fixture(scope="module")` | 模块内测试共享 |
| 会话级 | `@pytest.fixture(scope="session")` | 全局共享（仅 conftest.py 使用） |

### 1.3 Fixture 定义规范

```python
# conftest.py（公共 fixture）
@pytest.fixture
def mock_db_session():
    """提供 Mock 数据库会话"""
    with mock.patch("app.core.database.get_async_db") as mock_get_db:
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session
        yield mock_session

# 子模块 conftest.py（模块级 fixture）
@pytest.fixture
def zentao_adapter():
    """构造禅道适配器实例"""
    return ZentaoAdapter()

# 测试文件内 fixture（测试专用）
@pytest.fixture
def mock_zentao_client():
    """Mock 禅道 HTTP 客户端"""
    with mock.patch("app.integrations.sources.zentao.client.ZentaoClient") as mock:
        client = mock.return_value
        client.login = AsyncMock()
        client.get_tasks = AsyncMock(return_value=[])
        yield client
```

---

## 二、Mock 规范

### 2.1 Mock 策略选择

| 场景 | 工具 | 示例 |
|------|------|------|
| 阻止模块导入 | conftest.py 模块替换 | `sys.modules["app.core.database"] = _database` |
| Mock 函数返回值 | `unittest.mock.patch` | `@patch("app.services.user_service.get_user")` |
| Mock 异步函数 | `AsyncMock` | `AsyncMock(return_value=[])` |
| Mock 类实例方法 | `MagicMock` + `spec` | `MagicMock(spec=TaskSourceAdapter)` |
| Mock 环境变量 | `monkeypatch.setenv` | `monkeypatch.setenv("DATABASE_URL", "sqlite://")` |
| Mock HTTP 请求 | `httpx.MockTransport` | `httpx.MockTransport(handler)` |

### 2.2 Mock 命名规范

```python
# 变量名前缀标明 Mock
mock_user_service = MagicMock()
mock_db_session = AsyncMock()
mock_response = {"code": 200, "data": {}}

# patch 装饰器参数按函数参数顺序对应
@patch("app.services.user_service.get_user")
@patch("app.services.permission_service.check_permission")
def test_something(mock_check_perm, mock_get_user):
    # 注意：参数顺序 = 装饰器逆序
    pass
```

### 2.3 Mock 边界

| 禁止 | 说明 |
|------|------|
| ❌ 绕过 conftest.py 直接 import 真实 DB | 单元测试必须隔离 |
| ❌ Mock 被测函数自身 | 应 Mock 外部依赖 |
| ❌ Mock 非必要依赖 | 尽量少的 Mock |
| ❌ `MagicMock` 不指定 `spec` | 指定 `spec` 防止误用不存在的方法 |

### 2.4 Mock 验证

```python
# 验证调用
mock_client.login.assert_called_once()
mock_client.get_tasks.assert_called_once_with(project_id=123)

# 验证未调用
mock_email_service.send.assert_not_called()

# 验证调用参数
mock_service.create.assert_called_with(
    title="测试任务",
    status=TaskStatus.NEW,
)
```

---

## 三、前端 Mock 规范

### 3.1 全局 Mock（`frontend/src/test/setup.ts`）

当前已有：
```typescript
// localStorage Mock
const store: Record<string, string> = {};
window.localStorage = {
  getItem: vi.fn((key) => store[key] ?? null),
  setItem: vi.fn((key, value) => { store[key] = value; }),
  removeItem: vi.fn((key) => { delete store[key]; }),
  clear: vi.fn(() => { Object.keys(store).forEach(k => delete store[k]); }),
};

// matchMedia Mock
Object.defineProperty(window, 'matchMedia', {
  value: vi.fn().mockImplementation((query) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })),
});
```

**扩展规则**：新 Mock 统一加在 `setup.ts` 中，使用 `vi.fn()` 而非 `jest.fn()`。

### 3.2 API Mock

```typescript
// 使用 vi.spyOn Mock 模块级函数
import * as client from '@/api/client';

vi.spyOn(client, 'createRequest').mockReturnValue(
  vi.fn().mockResolvedValue({ code: 200, data: {} })
);

// Mock fetch
global.fetch = vi.fn().mockResolvedValue({
  ok: true,
  json: () => Promise.resolve({ code: 200, data: {} }),
});
```

### 3.3 Store Mock

```typescript
import { useAuthStore } from '@/stores/auth';

beforeEach(() => {
  useAuthStore.setState({
    isLoggedIn: false,
    username: null,
    token: null,
    isAdmin: false,
  });
});
```

---

## 四、AI 模块 Mock 规范

```python
# Mock LLM 客户端
@pytest.fixture
def mock_llm_client():
    with mock.patch("ai.core.llm.LLMClient") as mock:
        client = mock.return_value
        client.chat = AsyncMock(return_value="模拟回复")
        yield client

# Mock Embedding 模型
@pytest.fixture
def mock_embed():
    with mock.patch("ai.core.embed.get_embedding") as mock:
        mock.return_value = [0.1] * 768
        yield mock
```

---

## 五、相关文档

| 文档 | 路径 |
|------|------|
| 测试数据规范 | `test-data.md` |
| 目录结构规范 | `directory-structure.md` |
| 命名规范 | `naming-conventions.md` |
| 公共工具规范 | `utilities.md` |
| 测试总览 | `index.md` |
