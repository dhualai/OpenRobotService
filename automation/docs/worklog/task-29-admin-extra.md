# task-29 · admin 附加接口用例补齐

## 本次目标

补齐 admin 模块附加接口用例（permissions/users 扩展/roles 权限/daily-reports/projects 扩展/认证矩阵），新增 45 条。

## 已确认决策（grill）

Q1 P0+P1 精选（~55 条）| Q2 重复创建保持 409（差异记录）| Q3 risks GET 遮蔽保持 mock | Q4 权限模型保持简化

## 调研要点（子代理盘点 backend admin 15 个功能域）

- admin 共 65 个未覆盖路由；关键发现：真实后端存在路由遮蔽 bug（GET /projects/risks 被项目详情遮蔽返回 404）、DEBUG_MODE 下大部分接口无鉴权、重复创建状态码 400 vs mock 409
- permissions 是唯一 admin 硬校验域；users/roles 走细粒度权限（backend:xxx:write）

## 修改文件列表

- `automation/src/mocks/backend_mock.py`：
  - `_route_admin` 扩展：permissions 5 路由、users 扩展（detail/roles 分配移除/reporters/uspinfo/usp-username/options）、roles 扩展（auto-classify/permissions 系列/all-permissions）、daily-reports 6 路由（list/detail/by-date/search/PUT/DELETE）、projects me/licenses
  - 新增 handlers：`_route_admin_permissions`（含 seed 3 个权限）、`_seed_permissions`、`_route_admin_user_detail`、`_handle_admin_user_detail/roles/reporters/uspinfo/usp_username`、`_route_admin_role_detail/permissions`、`_route_admin_daily_report`、`_handle_admin_daily_report_by_date`、`_handle_admin_project_licenses`
  - 修复：daily-reports 列表尾斜杠匹配、detail 存在性校验（seed report id=1）
- 新增 `automation/tests/admin/test_admin_extra_code.py`（45 条：权限 9/用户扩展 14/角色权限 7/日报 7/项目扩展 5/认证矩阵 3）
- 更新 `automation/docs/testing/case-inventory-admin.md`（90 条，自动生成）
- 新增 `automation/docs/worklog/task-29-admin-extra.md`（本文）

## 测试结果

```
# 全量
427 passed, 28 skipped in 10.49s

# API 用例
219 passed（admin 90 + wechat 27 + integrations 16 + call 41 + tasks 32 + auth 13）
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`（219 条，六模块）

## 过程问题

- permissions/daily-reports 尾斜杠匹配（rest in ("/xxx", "/xxx/")）
- daily-report detail 无条件 200（补存在性校验）
- all-permissions 未进 permissions 分支（endswith 补充 + rest.index ValueError 修复）

## 记录在案的已知差异（mock vs 真实后端）

- 重复创建用户/角色：mock 409 / 真实 400（保持 mock）
- GET /projects/risks：mock 可达 / 真实被遮蔽 404（真实后端 bug，等修复后对齐）
- 权限模型：mock 简化（admin 才能访问）/ 真实细粒度 + DEBUG 无鉴权

## 下一步

- P1 DB 集成测试接 CI / 真实后端验证（等 MySQL 地址）
- resource-manager 21 路由 + folders + minio（P1，量大）
- 提交 git
