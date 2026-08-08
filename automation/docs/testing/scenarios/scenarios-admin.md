# 后台管理模块 - 测试场景设计（实际用例清单）

> 本清单由 `automation/scripts/cli-gen-scenario-docs.py` 从 `automation/testdata/cases/api-test-cases.xlsx` 自动生成，Excel 用例变化后请重跑脚本。

## 覆盖统计

| 覆盖类型 | 用例数 | 用例 ID |
|----------|--------|---------|
| 正常流程 | 27 | ADMIN-001, ADMIN-002, ADMIN-003, ADMIN-004, ADMIN-005, ADMIN-006, ADMIN-007, ADMIN-008, ADMIN-009, ADMIN-010, ADMIN-011, ADMIN-012, ADMIN-013, ADMIN-014, ADMIN-021, ADMIN-022, ADMIN-023, ADMIN-024, ADMIN-025, ADMIN-028, ADMIN-030, ADMIN-033, ADMIN-035, ADMIN-037, ADMIN-039, ADMIN-041, ADMIN-043 |
| 异常流程 | 9 | ADMIN-015, ADMIN-027, ADMIN-029, ADMIN-032, ADMIN-034, ADMIN-036, ADMIN-038, ADMIN-042, ADMIN-044 |
| 权限 | 3 | ADMIN-016, ADMIN-017, ADMIN-045 |
| 数据校验 | 3 | ADMIN-026, ADMIN-031, ADMIN-040 |
| Redis | 2 | ADMIN-018, ADMIN-019 |
| 数据库 | 1 | ADMIN-020 |

## 正常流程

| 用例ID | 接口 | 说明 |
|--------|------|------|
| ADMIN-001 | `GET /api/admin/tickets -> 200` | 正常流程：工单列表 |
| ADMIN-002 | `GET /api/admin/tickets/stats -> 200` | 正常流程：工单统计 |
| ADMIN-003 | `GET /api/admin/projects -> 200` | 正常流程：项目列表 |
| ADMIN-004 | `POST /api/admin/projects -> 200` | 正常流程：创建项目 |
| ADMIN-005 | `GET /api/admin/projects/risks -> 200` | 正常流程：风险列表 |
| ADMIN-006 | `GET /api/admin/dashboard/tickets/summary -> 200` | 正常流程：看板汇总 |
| ADMIN-007 | `GET /api/admin/users/ -> 200` | 正常流程：用户列表 |
| ADMIN-008 | `GET /api/admin/roles/ -> 200` | 正常流程：角色列表 |
| ADMIN-009 | `POST /api/admin/daily-reports -> 200` | 正常流程：生成日报 |
| ADMIN-010 | `POST /api/admin/daily-reports -> 200` | 正常流程：生成周报 |
| ADMIN-011 | `POST /api/admin/export/project/P001 -> 200` | 正常流程：导出数据 |
| ADMIN-012 | `GET /api/admin/resource-manager/resources -> 200` | 正常流程：资源列表 |
| ADMIN-013 | `POST /api/admin/resource-manager/resources -> 200` | 正常流程：创建资源 |
| ADMIN-014 | `GET /api/admin/resource-manager/resources/1 -> 200` | 正常流程：资源详情 |
| ADMIN-021 | `GET /api/admin/tickets/stats -> 200` | 正常流程：工单统计 |
| ADMIN-022 | `POST /api/admin/daily-reports -> 200` | 正常流程：生成日报 |
| ADMIN-023 | `POST /api/admin/export/project/P001 -> 200` | 正常流程：导出报表 |
| ADMIN-024 | `DELETE /api/admin/roles/1 -> 204` | 正常流程：删除角色 |
| ADMIN-025 | `POST /api/admin/users -> 201` | 正常流程：创建用户 |
| ADMIN-028 | `PUT /api/admin/users/testadmin -> 200` | 正常流程：更新用户 |
| ADMIN-030 | `POST /api/admin/roles -> 201` | 正常流程：创建角色 |
| ADMIN-033 | `PUT /api/admin/roles/1 -> 200` | 正常流程：更新角色 |
| ADMIN-035 | `PUT /api/admin/projects/1 -> 200` | 正常流程：更新项目 |
| ADMIN-037 | `DELETE /api/admin/projects/1 -> 204` | 正常流程：删除项目 |
| ADMIN-039 | `POST /api/admin/projects/risks -> 200` | 正常流程：创建风险 |
| ADMIN-041 | `PUT /api/admin/projects/risks/R1 -> 200` | 正常流程：更新风险 |
| ADMIN-043 | `DELETE /api/admin/projects/risks/R1 -> 204` | 正常流程：删除风险 |

## 异常流程

| 用例ID | 接口 | 说明 |
|--------|------|------|
| ADMIN-015 | `GET /api/admin/projects/999 -> 404` | 异常流程：项目详情不存在 |
| ADMIN-027 | `POST /api/admin/users -> 409` | 异常流程：用户名已存在 |
| ADMIN-029 | `PUT /api/admin/users/nobody -> 404` | 异常流程：用户不存在 |
| ADMIN-032 | `POST /api/admin/roles -> 409` | 异常流程：角色已存在 |
| ADMIN-034 | `PUT /api/admin/roles/999 -> 404` | 异常流程：角色不存在 |
| ADMIN-036 | `PUT /api/admin/projects/999 -> 404` | 异常流程：项目不存在 |
| ADMIN-038 | `DELETE /api/admin/projects/999 -> 404` | 异常流程：项目不存在 |
| ADMIN-042 | `PUT /api/admin/projects/risks/R999 -> 404` | 异常流程：风险不存在 |
| ADMIN-044 | `DELETE /api/admin/projects/risks/R999 -> 404` | 异常流程：风险不存在 |

## 权限

| 用例ID | 接口 | 说明 |
|--------|------|------|
| ADMIN-016 | `GET /api/admin/users -> 403` | 权限：非admin访问403 |
| ADMIN-017 | `GET /api/admin/users -> 401` | 权限：无token访问401 |
| ADMIN-045 | `POST /api/admin/users -> 401` | 权限：未认证创建用户 |

## 数据校验

| 用例ID | 接口 | 说明 |
|--------|------|------|
| ADMIN-026 | `POST /api/admin/users -> 422` | 数据校验：缺 username |
| ADMIN-031 | `POST /api/admin/roles -> 422` | 数据校验：缺 name |
| ADMIN-040 | `POST /api/admin/projects/risks -> 422` | 数据校验：缺 name |

## Redis

| 用例ID | 接口 | 说明 |
|--------|------|------|
| ADMIN-018 | `GET /api/admin/users -> 200` | Redis：用户列表缓存 |
| ADMIN-019 | `GET /api/admin/roles -> 200` | Redis：角色缓存失效 |

## 数据库

| 用例ID | 接口 | 说明 |
|--------|------|------|
| ADMIN-020 | `DELETE /api/admin/users/testadmin -> 204` | 数据库：删除用户级联事务 |

## 汇总表

| 用例ID | 接口 | 覆盖类型 | 说明 |
|--------|------|---------|------|
| ADMIN-001 | `GET /api/admin/tickets -> 200` | 正常流程 | 正常流程：工单列表 |
| ADMIN-002 | `GET /api/admin/tickets/stats -> 200` | 正常流程 | 正常流程：工单统计 |
| ADMIN-003 | `GET /api/admin/projects -> 200` | 正常流程 | 正常流程：项目列表 |
| ADMIN-004 | `POST /api/admin/projects -> 200` | 正常流程 | 正常流程：创建项目 |
| ADMIN-005 | `GET /api/admin/projects/risks -> 200` | 正常流程 | 正常流程：风险列表 |
| ADMIN-006 | `GET /api/admin/dashboard/tickets/summary -> 200` | 正常流程 | 正常流程：看板汇总 |
| ADMIN-007 | `GET /api/admin/users/ -> 200` | 正常流程 | 正常流程：用户列表 |
| ADMIN-008 | `GET /api/admin/roles/ -> 200` | 正常流程 | 正常流程：角色列表 |
| ADMIN-009 | `POST /api/admin/daily-reports -> 200` | 正常流程 | 正常流程：生成日报 |
| ADMIN-010 | `POST /api/admin/daily-reports -> 200` | 正常流程 | 正常流程：生成周报 |
| ADMIN-011 | `POST /api/admin/export/project/P001 -> 200` | 正常流程 | 正常流程：导出数据 |
| ADMIN-012 | `GET /api/admin/resource-manager/resources -> 200` | 正常流程 | 正常流程：资源列表 |
| ADMIN-013 | `POST /api/admin/resource-manager/resources -> 200` | 正常流程 | 正常流程：创建资源 |
| ADMIN-014 | `GET /api/admin/resource-manager/resources/1 -> 200` | 正常流程 | 正常流程：资源详情 |
| ADMIN-015 | `GET /api/admin/projects/999 -> 404` | 异常流程 | 异常流程：项目详情不存在 |
| ADMIN-016 | `GET /api/admin/users -> 403` | 权限 | 权限：非admin访问403 |
| ADMIN-017 | `GET /api/admin/users -> 401` | 权限 | 权限：无token访问401 |
| ADMIN-018 | `GET /api/admin/users -> 200` | Redis | Redis：用户列表缓存 |
| ADMIN-019 | `GET /api/admin/roles -> 200` | Redis | Redis：角色缓存失效 |
| ADMIN-020 | `DELETE /api/admin/users/testadmin -> 204` | 数据库 | 数据库：删除用户级联事务 |
| ADMIN-021 | `GET /api/admin/tickets/stats -> 200` | 正常流程 | 正常流程：工单统计 |
| ADMIN-022 | `POST /api/admin/daily-reports -> 200` | 正常流程 | 正常流程：生成日报 |
| ADMIN-023 | `POST /api/admin/export/project/P001 -> 200` | 正常流程 | 正常流程：导出报表 |
| ADMIN-024 | `DELETE /api/admin/roles/1 -> 204` | 正常流程 | 正常流程：删除角色 |
| ADMIN-025 | `POST /api/admin/users -> 201` | 正常流程 | 正常流程：创建用户 |
| ADMIN-026 | `POST /api/admin/users -> 422` | 数据校验 | 数据校验：缺 username |
| ADMIN-027 | `POST /api/admin/users -> 409` | 异常流程 | 异常流程：用户名已存在 |
| ADMIN-028 | `PUT /api/admin/users/testadmin -> 200` | 正常流程 | 正常流程：更新用户 |
| ADMIN-029 | `PUT /api/admin/users/nobody -> 404` | 异常流程 | 异常流程：用户不存在 |
| ADMIN-030 | `POST /api/admin/roles -> 201` | 正常流程 | 正常流程：创建角色 |
| ADMIN-031 | `POST /api/admin/roles -> 422` | 数据校验 | 数据校验：缺 name |
| ADMIN-032 | `POST /api/admin/roles -> 409` | 异常流程 | 异常流程：角色已存在 |
| ADMIN-033 | `PUT /api/admin/roles/1 -> 200` | 正常流程 | 正常流程：更新角色 |
| ADMIN-034 | `PUT /api/admin/roles/999 -> 404` | 异常流程 | 异常流程：角色不存在 |
| ADMIN-035 | `PUT /api/admin/projects/1 -> 200` | 正常流程 | 正常流程：更新项目 |
| ADMIN-036 | `PUT /api/admin/projects/999 -> 404` | 异常流程 | 异常流程：项目不存在 |
| ADMIN-037 | `DELETE /api/admin/projects/1 -> 204` | 正常流程 | 正常流程：删除项目 |
| ADMIN-038 | `DELETE /api/admin/projects/999 -> 404` | 异常流程 | 异常流程：项目不存在 |
| ADMIN-039 | `POST /api/admin/projects/risks -> 200` | 正常流程 | 正常流程：创建风险 |
| ADMIN-040 | `POST /api/admin/projects/risks -> 422` | 数据校验 | 数据校验：缺 name |
| ADMIN-041 | `PUT /api/admin/projects/risks/R1 -> 200` | 正常流程 | 正常流程：更新风险 |
| ADMIN-042 | `PUT /api/admin/projects/risks/R999 -> 404` | 异常流程 | 异常流程：风险不存在 |
| ADMIN-043 | `DELETE /api/admin/projects/risks/R1 -> 204` | 正常流程 | 正常流程：删除风险 |
| ADMIN-044 | `DELETE /api/admin/projects/risks/R999 -> 404` | 异常流程 | 异常流程：风险不存在 |
| ADMIN-045 | `POST /api/admin/users -> 401` | 权限 | 权限：未认证创建用户 |
