# OpenAPI 契约测试

契约测试使用 Schemathesis 4.x，通过 FastAPI 暴露的 OpenAPI Schema 生成用例。

## 本地运行

```powershell
cd D:\WorkCode\OpenRobotService
pip install -e automation

$env:CONTRACT_TEST_ENABLED = "1"
$env:OPENAPI_SCHEMA_URL = "http://localhost:8400/api/openapi.json"
$env:OPENAPI_BASE_URL = "http://localhost:8400"
$env:CONTRACT_INCLUDE_PATH_REGEX = "^/api/(health|auth/login)$"
pytest automation/tests/contract -m contract -v
```

## 参数

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `CONTRACT_TEST_ENABLED` | `0` | 必须设为 `1` 才执行 |
| `OPENAPI_SCHEMA_URL` | `<base_url>/api/openapi.json` | Schema 地址 |
| `OPENAPI_BASE_URL` | `config.api.base_url` | API 地址 |
| `CONTRACT_INCLUDE_PATH_REGEX` | `^/api/(health|auth/login)$` | 第一版只测安全路径 |
| `CONTRACT_PHASES` | `examples,coverage` | Schemathesis 阶段 |
| `CONTRACT_MODE` | `positive` | `positive / negative / all` |
| `CONTRACT_MAX_EXAMPLES` | `5` | 每个操作的用例数 |

## 运行策略

- PR Fast Lane：默认不跑，避免写真实数据。
- test 分支 / 人工触发：运行 health、登录等只读契约。
- 夜间：可扩大 `CONTRACT_INCLUDE_PATH_REGEX`，运行更多接口。
- 不允许在未清理数据的情况下直接用 `mode=all` 对生产环境跑写接口。