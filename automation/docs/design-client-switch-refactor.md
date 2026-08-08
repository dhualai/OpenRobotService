# 设计：客户端可切换 + 401/403 语义对齐（D1/D2）

> 关联分析文档：`automation/docs/gap-analysis-framework-confirmation.md`
> 状态：设计稿，待人工确认后才进入实现

---

## 一、目标

1. 同一套 Excel 用例可在 MockBackend 与真实后端之间切换（`USE_MOCK` 环境变量，默认 mock）
2. 统一 401/403 语义：默认返回响应给断言；显式开启时抛 `AuthenticationError`
3. 全部既有用例零改动

## 二、涉及文件清单

| 文件 | 改动类型 |
|------|----------|
| `automation/src/clients/api_client.py` | 修改（transport 注入 + raise_auth_errors） |
| `automation/tests/conftest.py` | 修改（mock_api_client 基于 ApiClient + USE_MOCK 开关） |
| `automation/src/clients/tests/test_api_client.py` | 修改（适配 401 测试 + 新增切换测试） |
| `automation/AGENTS.md` | 修改（Allure 模块清单 + auth） |
| `automation/docs/automation_strategy.md` | 修改（CI 章节更新） |

## 三、模块职责划分与改动设计

### 3.1 `src/clients/api_client.py` — 客户端抽象层

```python
def __init__(self, config: Optional[ApiConfig] = None,
             retry_config: Optional[RetryConfig] = None,
             transport: Optional[httpx.AsyncBaseTransport] = None,
             raise_auth_errors: bool = False):
    self._transport = transport
    self._raise_auth_errors = raise_auth_errors
    ...

async def connect(self):
    self._client = httpx.AsyncClient(
        base_url=self._cfg.base_url,
        transport=self._transport,   # None = 真实网络
        timeout=httpx.Timeout(self._cfg.timeout),
    )
    ...

# request() 中 401/403 处理：
if response.status_code in (401, 403) and self._raise_auth_errors:
    raise AuthenticationError(f"Authentication failed: ...")
```

- `transport=None`：走真实网络（现状行为不变）
- `transport=MockTransport`：走 MockBackend
- `raise_auth_errors=False`（默认）：401/403 正常返回响应 → 与 mock 路径语义一致，权限用例统一断言 401
- `raise_auth_errors=True`：保留既有抛异常能力（显式开启）

### 3.2 `tests/conftest.py` — mock_api_client fixture 改造

```python
@pytest.fixture
async def mock_api_client():
    use_mock = os.getenv("USE_MOCK", "1") != "0"
    if use_mock:
        client = ApiClient(config=ApiConfig(base_url="http://mock.local", timeout=30),
                           transport=create_mock_transport(),
                           raise_auth_errors=False)
    else:
        client = ApiClient(config=load_config().api, raise_auth_errors=False)
    await client.connect()
    yield client
    await client.close()
```

- fixture 名称、签名不变 → call/tasks/admin/auth 全部用例零改动
- `USE_MOCK=0` 时走真实后端（`config.yaml` 的 `api.base_url`），同套用例验证真实环境
- 顺带收益：mock 用例也获得 ApiClient 的日志/Allure 附件封装

### 3.3 `src/clients/tests/test_api_client.py`

- 既有 `test_request_authentication_error`：改为 `raise_auth_errors=True` 时抛 `AuthenticationError`
- 新增：`raise_auth_errors=False`（默认）时 401 返回响应
- 新增：transport 注入测试（注入 MockTransport，请求命中 MockBackend 并返回其响应）

### 3.4 `automation/AGENTS.md`

- 「Allure 报告只包含 API 测试用例（call/tasks/admin 三个模块）」→ 更新为 `call/tasks/admin/auth` 四个模块
- 报告生成命令示例同步补充 auth

### 3.5 根 `automation/docs/automation_strategy.md`

- 第五节「CI/CD 集成（待建设）」→ 更新为已上线：`.github/workflows/test.yml`（后端/前端/自动化测试，MySQL/Redis/Qdrant 容器）、`.github/workflows/ai-test.yml`（AI 用例生成+执行）

## 四、实现步骤（顺序执行，一次一个模块）

1. **ApiClient 改造**（`api_client.py`）：transport 参数 + raise_auth_errors 参数
2. **测试适配**（`test_api_client.py`）：更新 401 测试 + 新增 3 个测试
   - 验证：`cd automation && pytest src/clients -v`（Fast Lane）
3. **fixture 改造**（`tests/conftest.py`）：mock_api_client 基于 ApiClient + USE_MOCK 开关
   - 验证：`cd automation && pytest tests/ -m api -v`（Full Lane 全量回归，93 用例）
4. **文档更新**（AGENTS.md + automation/docs/automation_strategy.md）
5. **收尾验证**：Allure 报告生成 + worklog

## 五、风险分析

| 风险 | 等级 | 缓解 |
|------|------|------|
| ApiClient 对 mock transport 的 Allure 附件与 executor 内附件重复 | 低 | 仅报告展示冗余，不影响断言，接受 |
| `USE_MOCK=0` 时真实后端未启动 → 用例报 `ClientConnectionError` | 中 | 属预期行为；文档注明「切真实后端需先启动服务」 |
| mock transport 下 `client.connect()` 不发请求，行为差异 | 无 | connect 仅创建客户端，无副作用 |
| 401 权限用例依赖断言而非异常（`expected_status: 401` 校验） | 无 | 默认 `raise_auth_errors=False` 与此一致 |

## 六、验收标准

- [ ] `pytest src/clients -v` 全部通过
- [ ] `pytest tests/ -m api -v` 93 用例全部通过（或与现状一致）
- [ ] 新增测试覆盖：默认 401 返回响应 / True 抛异常 / transport 注入
- [ ] Allure 报告正常生成，含 call/tasks/admin/auth 四模块
- [ ] 文档同步完成
