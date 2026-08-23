# task-23 · 客户端可切换重构（mock ↔ 真实后端）+ 401/403 语义对齐

## 本次目标

1. `ApiClient` 支持 `transport` 注入，`mock_api_client` fixture 基于 `ApiClient` 封装，同一套 Excel 用例可通过 `USE_MOCK` 环境变量切换 mock/真实后端
2. 统一 401/403 语义：默认返回响应给断言（权限用例），`raise_auth_errors=True` 时保留抛异常能力
3. 文档同步（AGENTS.md Allure 四模块 + USE_MOCK 说明、automation/docs/automation_strategy.md CI 章节）

## 阅读内容

- `automation/src/clients/api_client.py`（客户端封装、401/403 处理）
- `automation/src/utils/retry.py`（确认重试仅针对连接/超时异常，与 401 无冲突）
- `automation/src/runner/executor.py`（确认 run_case 仅依赖 `.request()` 鸭子类型）
- `automation/tests/conftest.py`（mock_api_client 现状：裸 httpx.AsyncClient）
- `automation/src/clients/tests/test_api_client.py`（既有 401 测试）
- `automation/config/__init__.py`（load_config / ApiConfig 导出）

## 修改文件列表

- `automation/src/clients/api_client.py`：`__init__` 增加 `transport` / `raise_auth_errors` 参数；`connect()` 透传 transport；401/403 仅在 `raise_auth_errors=True` 时抛 `AuthenticationError`
- `automation/src/clients/tests/test_api_client.py`：`test_request_authentication_error` → `test_request_authentication_error_when_enabled`（显式 True）；新增 `test_request_returns_401_response_by_default`、`test_transport_injection_hits_mock_backend`
- `automation/tests/conftest.py`：`mock_api_client` 基于 `ApiClient`（默认 `USE_MOCK=1` 注入 MockTransport；`USE_MOCK=0` 走真实后端）；移除裸 httpx 用法
- `automation/AGENTS.md`：Allure 模块清单三→四模块（+auth）；新增「目标切换（Mock ↔ 真实后端）」小节
- `automation/docs/automation_strategy.md`：第五节「CI/CD 集成（待建设）」→「CI/CD 集成」（已上线，含 workflows 与本地 bat 通道）
- 新增 `automation/docs/gap-analysis-framework-confirmation.md`（差异分析）
- 新增 `automation/docs/design-client-switch-refactor.md`（设计稿）
- 新增 `automation/docs/worklog/task-23-client-switch-refactor.md`（本文）

## 测试结果

```
# Fast Lane（客户端）
43 passed in 2.17s

# Full Lane（API 用例，默认 mock）
93 passed in 1.59s

# 全量
263 passed, 28 skipped in 6.14s   # 28 skip 为 AI 用例（tenacity/AI 服务未起，预期）

# USE_MOCK=0 冒烟（真实后端未启动）
32 errors（连接失败，证明切换路径真实生效，属预期）
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`（93 用例，含 call/tasks/admin/auth 四模块）

## 风险

- 切换真实后端需先启动后端服务（`config.yaml` 的 `api.base_url`），未启动时报连接错误（预期）
- mock 路径现在附带 ApiClient 的日志/Allure 附件封装，与 executor 内附件存在轻微重复（仅报告冗余，不影响断言）

## 下一步建议

- P1：MySQL/Redis/Qdrant 客户端集成测试接入 CI（容器服务已就绪）
- P2：AI 评测用例按需补充 tenacity 依赖并接 AI 服务联调
- P3：Playwright UI/E2E 用例建设（依赖已装）
