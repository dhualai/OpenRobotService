# task-24 · call + admin 模块 CRUD/场景用例补齐 + 路径对齐

## 本次目标

1. 补齐 call 模块 conversations/messages/my-tasks 的缺失 CRUD 用例（16 条，含 1 条全链路 steps）
2. 补齐 admin 模块 users/roles/projects/risks 的 CRUD 用例（21 条，含权限）
3. 路径对齐：mock/Excel 与真实后端契约一致（auth 补 `/api`、call 补 `/call` 段、admin 导出/资源对齐）
4. 场景文档与 Excel 实际用例保持一致

来源：grill 三轮确认（Q1~Q10）+ 设计文档 `automation/docs/design-cases-call-admin.md`（已人工确认）。

## 阅读内容

- `automation/src/mocks/backend_mock.py`（路由注册、seed 数据、校验逻辑）
- `automation/src/runner/executor.py`（`_auth_for_role` 登录路径、steps 执行）
- `automation/tests/conftest.py`（mock_api_client fixture、登录路径）
- `automation/testdata/cases/api-test-cases.xlsx`（call/admin 用例现状）
- `automation/docs/testing/scenarios/scenarios-call.md` / `scenarios-admin.md`（原设计稿）
- `automation/src/runner/tests/test_runner.py`（框架库测试）

## 修改文件列表

- `automation/src/mocks/backend_mock.py`
  - 路径修正：`/auth/login`→`/api/auth/login`、`/auth/me`→`/api/auth/me`、call 全系加 `/call` 段、`/export`→`/export/project/`、`/resources`→`/resource-manager/resources`
  - 路由扩展：conversations PUT/DELETE、messages GET{id}/PUT/DELETE、my-tasks GET{task_id}、projects PUT/DELETE、risks POST/PUT/DELETE
  - 校验补充：conversations PUT 空 title 422、messages PUT 空 content 422、messages GET 缺 conversation_id 422
  - seed 补充：`_messages[1]`、`_admin_risks["R1"]`；修复 `_messages` 懒初始化 AttributeError
- `automation/src/runner/executor.py`：`_auth_for_role` 登录路径 `/auth/login`→`/api/auth/login`
- `automation/tests/conftest.py`：`mock_auth_token` 登录路径同上
- `automation/src/runner/tests/test_runner.py`：适配 steps 用例（无顶层 method）；auth 路径更新
- `automation/testdata/cases/api-test-cases.xlsx`：29 条路径修正 + call 新增 16 条（CALL-026~041）+ admin 新增 21 条（ADMIN-025~045）；修复 ADMIN-011/023 export 路径二次替换损坏
- 新增 `automation/scripts/cli-update-cases-xlsx.py`：Excel 路径批量修正（幂等）
- 新增 `automation/scripts/cli-append-cases.py`：Excel 用例追加（按模块，内置用例定义）
- 新增 `automation/scripts/cli-gen-scenario-docs.py`：从 Excel 生成场景文档（可复用）
- 重写 `automation/docs/testing/scenarios/scenarios-call.md` / `scenarios-admin.md`：与 Excel 实际一致（原文档为旧设计稿，错位已校正）
- 新增 `automation/docs/design-cases-call-admin.md`（设计稿）、`automation/docs/worklog/task-24-call-admin-cases.md`（本文）

## 测试结果

```
# 全量（框架库 + 用例）
300 passed, 28 skipped in 9.68s     # 28 skip 为 AI 用例（tenacity/AI 服务未起，预期）

# Allure 报告通道（call/tasks/admin/auth 四模块）
130 passed in 12.33s                # call 41 + tasks 32 + admin 45 + auth 12

# 分模块（路径修正回归）
call 41 passed / admin 45 passed    # 新增用例一次落地通过
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`（130 用例，四模块）

## 风险

- ADMIN-011/023 曾被脚本二次替换为 `/export/project/P001/project/P001`（幂等锚点修复前误伤），已修复并验证脚本幂等（连续两次运行 0 变更）
- `request.url.query` 在 httpx 中返回 bytes，已用 `str()` 包装（CALL-031/032 曾报 TypeError）
- users/roles 的 POST/PUT mock 已支持（盘点结论），本次仅补 Excel 用例

## 下一步建议

- P1：`USE_MOCK=0` 冒烟验证真实后端（路径已对齐，需启动后端服务）
- P2：wechat/integrations 模块路径与用例补齐（Q7 确认留到下轮）
- P3：AI 评测用例接真实服务（tenacity + 8401）
