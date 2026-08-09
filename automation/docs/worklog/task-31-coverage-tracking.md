# task-31 · 看板/校验用例补齐 + 产品场景覆盖追踪

## 本次目标

1. 补齐 dashboard 4 接口 + 项目/角色校验分支用例（6 条）
2. 归档「AI服务号-测试场景.xlsx」并建立产品场景 → 测试用例覆盖映射表

## 一、用例补齐

- `automation/src/mocks/backend_mock.py`：
  - `_handle_admin_projects_create`：缺 name/project_code → 422
  - `_handle_admin_roles_detail`：删除内置角色（id 1/2）→ 400（有用户使用）
- 新增 `TestDashboardExtra`（4 条：dashboard/tickets、projects/summary、projects/urgency、projects）+ `TestValidationExtra`（2 条：项目缺名 422、删除内置角色 400）
- 适配现有用例：`test_projects_create` 补 project_code、`test_roles_delete` 改为创建→删除（内置角色不再可删）

## 二、覆盖追踪

- 归档 `references/ai-service-test-scenarios.xlsx`（产品场景清单，124 条）
- 新增 `scripts/cli-gen-scenario-coverage.py`：读场景 Excel + 内置映射 → 生成 `docs/testing/scenario-coverage.md`
- 映射结论：**124 条产品场景，已覆盖 54 条（43%）**；未覆盖分布：纯 UI 前端（~35 条，P3 Playwright 范畴）、后端无 API（~15 条，需反馈产品/后端）、AI 语义（~8 条，评测可扩展）、业务规则派单（~6 条，依赖真实分工配置）

## 测试结果

```
# 全量
463 passed, 28 skipped in 10.59s
# admin 清单
126 cases（+6）
```

## 过程问题

- 覆盖脚本 header 提取 bug：`ws[1]` 返回 Cell 对象而非值（str(c.value) 修复）
- 内置角色删除语义变更影响现有 test_roles_delete（改创建→删除流程）

## 下一步

- 真实后端验证 / P1 DB 集成（等 MySQL）
- 覆盖映射表可作为每次任务完成后的更新项（跑 cli-gen-scenario-coverage.py）
