# 框架梳理确认 · 差异分析

> 来源：框架梳理确认会话（2026-08-07）
> 状态：已完成分析，待人工确认设计

---

## 一、背景

对 `automation/` 自动化测试框架进行系统性梳理，并以对话方式逐步确认核心设计意图。本文件记录「设计意图 vs 代码现状」的差异，作为整改依据。

## 二、已确认的设计意图（与现状一致）

| # | 设计决策 | 现状 |
|---|----------|------|
| 1 | 五层结构：框架库 `src/` + 配置 `config/` + 用例 `tests/` + 数据 `testdata/` + 工具 `scripts/ci/` | ✅ |
| 2 | Excel 为主、Python 为辅的数据驱动（`load_cases` + `parametrize`） | ✅ |
| 3 | MockBackend（httpx.MockTransport，7 功能域）支撑 P0 API 用例 | ✅ |
| 4 | AI 用例跳过（缺 tenacity / AI 服务 8401 未起）是预期状态 | ✅ 长期预期，不整改 |
| 5 | CI 已上线（test.yml / ai-test.yml，含 MySQL/Redis/Qdrant 容器服务） | ✅ 已落地 |
| 6 | 金字塔：P0 mock API → P1 DB 集成（客户端已实现）→ P2 AI 评测（雏形）→ P3 UI/E2E（待建） | ✅ 状态确认 |
| 7 | Allure 报告应包含 call/tasks/admin/auth 四模块 | ⚠️ 文档漏 auth |

## 三、差异清单（需整改）

| # | 差异 | 严重度 | 现状 | 目标 |
|---|------|--------|------|------|
| D1 | Mock↔真实后端不可切换 | 高 | Excel 用例 fixture `mock_api_client` 使用裸 `httpx.AsyncClient(transport=MockTransport)`（`tests/conftest.py:22`），真实路径使用 `ApiClient`（`src/clients/api_client.py`），两套客户端互不兼容，用例写死 mock 目标 | 重构 `ApiClient` 支持注入 transport；`mock_api_client` 基于 `ApiClient` 封装；`USE_MOCK` 环境变量切换真实后端 |
| D2 | 401/403 语义不一致 | 中 | 真实 `ApiClient.request` 对 401/403 抛 `AuthenticationError`（`api_client.py:81`）；mock 路径返回响应交给断言（Excel 权限用例期望 401 响应） | 对齐为可配置：`raise_auth_errors` 默认 `False` 返回响应，`True` 时抛异常（保留既有能力） |
| D3 | `automation/docs/automation_strategy.md` CI 章节过时 | 低 | 第五节仍写「当前项目未配置 CI workflows」 | 更新为 CI 已上线描述 |
| D4 | `automation/AGENTS.md` Allure 模块清单漏 auth | 低 | 写「只包含 call/tasks/admin 三个模块」 | 更新为四模块（+auth） |
| D5 | AI 用例依赖缺失 | 低 | `tenacity` 未安装导致 `test_assigner`/`test_rag` 跳过 | 确认长期预期，不整改（记录在案） |

## 四、影响面分析

- `D1` 涉及文件：`src/clients/api_client.py`、`tests/conftest.py`、`src/clients/tests/test_api_client.py`
- `D2` 涉及文件：`src/clients/api_client.py`、`src/clients/tests/test_api_client.py`（现有 401 测试需适配）
- `run_case` 执行器仅依赖客户端 `.request()`（鸭子类型，见 `src/runner/executor.py:243`），改造客户端不影响执行器
- retry 机制仅针对 `ClientConnectionError`/`ClientTimeoutError`（`src/utils/retry.py:22-24`），与 401 处理无冲突
- 全部 Excel 用例（call/tasks/admin/auth）通过 `mock_api_client` fixture 获取客户端，fixture 内部改造后**用例零改动**

## 五、结论

框架整体健康，主要问题集中在客户端抽象层的目标切换与异常语义统一，可一次性重构解决，不影响既有用例。
