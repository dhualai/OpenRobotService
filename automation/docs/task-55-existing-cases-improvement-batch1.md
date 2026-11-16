# Task 55：存量用例改善第一批（弱断言 + 凭据收敛）

- **日期**：2026-11-16
- **状态**：完成（本地验证通过，待 CI workflow_dispatch 预验证）
- **遵循流程**：分析 → 设计 → 确认 → 实现 → 测试 → 文档（用户 19:06 确认方案）

## 本次目标

不新增用例，改善存量用例质量：
- P0-1 补齐成功路径弱断言
- P0-2 收敛散落的硬编码测试凭据

## 阅读内容

- `automation/AGENTS.md`（工作规范）
- `automation/docs/testing/analysis/` 已有分析文档
- `src/mocks/backend_mock.py`（逐一核对 29 处断言端点的返回结构）
- `src/assertions/data.py`（确认 `expected_fields` 只支持 dict 子集）
- `tests/conftest.py`（确认 mock fixture 为 function 作用域，数据隔离）
- `backend/scripts/seed_automation_env.py`（凭据默认值对齐）
- 5 个测试文件全文

## 分析与设计文档

- 分析：`docs/testing/analysis/analysis-existing-cases-assertions.md`
- 设计：`docs/testing/scenarios/design-existing-cases-improvement.md`

## 关键决策

精扫发现 29 处"成功路径仅验 200"（报告估 31），但逐条核对 mock 返回后分层处理：
- **可补真断言（14 处）**：返回为固定 dict 或稳定字段（page/size、ai-assign 固定值、resources total=0、wechat tags 等）
- **加注释说明（17 处）**：顶层 list 响应（`expected_fields` 仅支持 dict）、动态计数（total 随用例变化）、SSE 流式——强行补断言会让用例变脆
- 未采用报告里"补 31 处"的粗放做法，改为"能补则补、不能补则注明原因"

## 修改文件列表

| 文件 | 变更 |
|---|---|
| `src/config/credentials.py`（新建） | 统一凭据入口 `role_credentials(role)`，环境变量优先，默认值对齐 Mock 用户表 |
| `tests/call/test_call_code.py` | 凭据收敛 + 4 处字段断言 + 6 处注释 |
| `tests/tasks/test_tasks_code.py` | 凭据收敛 + 6 处字段断言 + 2 处注释 |
| `tests/admin/test_admin_code.py` | 凭据收敛 + 1 处字段断言 + 9 处注释 |
| `tests/auth/test_auth_code.py` | 3 处正确登录凭据收敛 + 2 处字段断言（故意错误凭据的用例保留原样） |
| `tests/business_chain/test_call_to_ticket_close.py` | U1/U2 凭据收敛 |

未改 `conftest.py`（Mock 内置用户属契约）、未改业务代码。

## 测试结果

```
# 逐模块（改造后立即验证）
tests/call/           41 passed
tests/tasks/          32 passed
tests/admin/          45 passed, 12 skipped（本地无 MySQL，DB 用例按设计跳过）
tests/auth/           12 passed
tests/business_chain/  1 passed

# 全量改动范围（含 Allure 数据收集）
131 passed, 12 skipped in 51.66s

# Allure HTML 报告
Report successfully generated to output\allure-report
```

## 自检结果

- ✅ 测试侧凭据明文零残留（grep cust123/eng123/123456/_ROLE_CREDENTIALS 无命中）
- ✅ git status 确认仅改 automation/（5 modified + 4 new）
- ✅ 新增字段断言 65 处（含原有），注释说明 17 处
- ✅ 只改 6 个测试/源文件，未超 10 文件阈值

## 风险

- 全量 `pytest tests/ -m api` 在本地因 `playwright` 未安装导致 UI 目录收集报错（与本批改动无关）；按 AGENTS.md 规范只跑四个 API 模块 + business_chain，全绿。
- 本地无 MySQL，12 条 DB 用例 skip；真实验证依赖 CI db-tests。
- 新增字段断言在本地 Mock 全通过；真实后端契约是否一致需 workflow_dispatch 预验证。

## 下一步计划

1. 提交改动到分支，走 workflow_dispatch 预验证（ci-guard + db-tests + deploy-split）。
2. 若 CI 全绿，刷新领导版报告的"断言改善"状态。
3. 第二批：P1-3 business_chain allure.step 结构化、P1-4 AI skip 防假绿、P1-5 契约车道。
