# task-26 · 代码驱动迁移（试点 + 全面迁移）

## 本次目标

按设计 `automation/docs/design-code-driven-migration.md`（grill 8 项决策确认），将全部用例从 Excel 数据驱动迁移为**代码驱动自由 pytest 函数**：tasks 试点验证 → 全面迁移 call/admin/auth。

## 已确认决策（grill 两轮）

Q1 Excel 保留为只读清单 | Q2 自由函数 | Q3 ci_ai_gen 本轮不动 | Q4 渐进试点 | Q5 复用 assertions 库 | Q6 class 分组 | Q7 直接装饰器 | Q8 executor 保留

## 报告优化迭代（用户逐轮确认）

1. **步骤树**：`_api()` helper 用 `with allure.step()` 包裹每个请求；全链路用例语义化步骤名（Step 1: 创建工单...）
2. **断言进报告**：`assertions` 库断言产生可见信息
3. **断言嵌套步骤内**：`_api` 增加 `expected_status`/`expected_fields` 参数，断言在请求 step 内执行（AST 脚本批量重组 52 处独立断言）
4. **附件收敛**：每请求 = **Request 1 + Response 1 + 断言信息 1**（新增 `src/assertions/report.py` contextvar 聚合缓冲，断言只记录、步骤结束时统一 flush；executor 同步接入）

## 修改文件列表

- 新增 `automation/tests/tasks/test_tasks_code.py`（32 条）、`tests/call/test_call_code.py`（41 条）、`tests/admin/test_admin_code.py`（45 条）、`tests/auth/test_auth_code.py`（12 条）
- 新增 `automation/src/assertions/report.py`（断言附件聚合缓冲）
- 修改 `automation/src/assertions/response.py`、`data.py`（断言记录到缓冲，不直接 attach）
- 修改 `automation/src/clients/api_client.py`（请求/响应附件合并为各 1 个）
- 修改 `automation/src/runner/executor.py`（删除重复附件、接入 flush）
- 新增 `automation/scripts/cli-gen-case-inventory.py` + 4 份清单文档（case-inventory-tasks/call/admin/auth.md）
- 新增 `automation/docs/design-code-driven-migration.md`、`automation/docs/worklog/task-26-code-driven-pilot.md`

## 测试结果

```
# 代码驱动版（4 模块 130 条）
tasks 32 + call 41 + admin 45 + auth 12 = 130 passed

# 全量（Excel 版 + 代码版并存）
260 passed, 28 deselected in 4.21s
# 框架库
170 passed
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`（260 条，含双版本）

## 附件结构（每请求统一）

```
Request（method/url/headers/body）→ Response（status/headers/body）→ 断言信息（状态码+全部字段合并 1 个）
```

## 下一步

- 代码驱动版稳定后：退役 Excel 版测试文件与 Excel 数据源（保留只读清单）
- ci_ai_gen 流水线输出端改为生成 Python 用例（单独一轮）
- 场景文档生成器 cli-gen-scenario-docs.py 数据源切换

