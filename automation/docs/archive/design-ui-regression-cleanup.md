# UI 回归测试数据清理设计

> 状态：已确认并实现数据库补偿；真实库执行待提供最小权限账号
> 日期：2026-09-19
> 目标环境：TestOpenRobotService / helpdesk_test
> 范围：测试工单和会话清理，不触碰生产环境

## 1. 当前问题

每次 UI 回归都会创建一条真实测试工单。当前流程是：

```text
UI 场景结束
-> CleanupManager 使用管理员账号调用 DELETE /api/tasks/{id}
-> 测试环境返回 HTTP 500
-> 清理失败只写入 Allure 告警
-> 测试工单残留
```

目前残留工单已累计到 `812` 之后的编号，继续接 CI 会持续扩大残留数据。

## 2. 已只读检查的文件

- `automation/src/ui_regression/cleanup.py`
- `automation/scripts/cli-cleanup-real-test-data.py`
- `backend/app/modules/tasks/api/task.py`
- `backend/app/modules/tasks/services/ticket_service.py`
- `backend/app/models/task.py`
- `backend/app/models/task_dispatch_log.py`
- `automation/docs/worklog/task-36-real-ticket-lifecycle.md`
- `automation/docs/worklog/task-46-ui-regression-execution-foundation.md`
- `automation/docs/worklog/task-48-combined-allure-report.md`

只读阅读业务代码，不允许在自动化任务中直接修改 `backend/`。

## 3. 根因分析

### 3.1 CleanupManager

当前 `CleanupManager` 只做两件事：

1. 管理员调用 `DELETE /api/tasks/{ticket_id}` 删除工单。
2. U1 调用 `DELETE /api/call/conversations/{conversation_id}` 删除会话。

会话删除当前返回 200，工单删除返回 500。

### 3.2 后端管理员删除逻辑

`TicketService.delete_ticket()` 在管理员模式下只显式执行：

```text
DELETE task_comments WHERE task_id = ?
DELETE tasks WHERE id = ?
```

其他子表主要依赖数据库外键 `ON DELETE CASCADE`。

如果测试数据库中的外键没有完整级联、表结构版本不一致，或者存在未显式删除的子表，
`DELETE tasks` 就会因为外键约束失败，最终被 API 转为 HTTP 500。

### 3.3 现有临时清理脚本

`cli-cleanup-real-test-data.py` 的优点：

- 默认 dry-run。
- 必须显式 `--execute` 才删除。
- 只删除匹配 task 及已知子表。

当前缺口：

1. 强限制 `--prefix` 必须以 `AUTO-` 开头。
2. UI 回归工单标题是 `自动化链路验证-*`，因此当前脚本匹配不到。
3. 只删除：
   - `task_comment_read_record`
   - `task_comments`
   - `task_dispatch_log`
   - `task_operation_logs`
   - `tasks`
4. 未处理：
   - `task_comment_read`
   - `task_followers`
   - `task_participants`
   - `task_spec_doc`

因此现有脚本不能作为当前 UI 回归清理的完整兜底。

## 4. 任务相关表清单

根据模型只读盘点，删除 task 时需要覆盖：

| 表 | 关联字段 | 当前模型 FK |
|---|---|---|
| `task_comment_read_record` | `task_id` | `ON DELETE CASCADE` |
| `task_comments` | `task_id` | `ON DELETE CASCADE` |
| `task_comment_read` | `task_id` | 无外键 |
| `task_dispatch_log` | `task_id` | `ON DELETE CASCADE` |
| `task_operation_logs` | `task_id` | `ON DELETE CASCADE` |
| `task_followers` | `task_id` | `ON DELETE CASCADE` |
| `task_participants` | `task_id` | `ON DELETE CASCADE` |
| `task_spec_doc` | `task_id` | `ON DELETE CASCADE` |
| `tasks` | 主记录 | 主表 |

数据库实际结构仍应在实现前使用只读查询核对，不能只依赖模型声明。

## 5. 方案比较

### 方案 A：只修后端删除接口

优点：

- 长期最干净。
- 自动化只需要继续使用产品接口。
- 不暴露数据库删除权限。

缺点：

- 涉及 `backend/` 业务代码，需要后端负责人 review 和发布。
- 时效不可控，阻塞当前回归和 CI。

### 方案 B：只扩展数据库补偿脚本

优点：

- 只修改自动化目录，当前团队可以快速推进。
- 可以对历史残留做精确清理。

缺点：

- 需要 MySQL 删除权限。
- 删除逻辑必须非常严格，误删风险高。

### 方案 C：API 优先，数据库补偿兜底

推荐采用。

```text
先调用管理员 DELETE API
-> 成功：记录已清理
-> 失败：记录告警
-> 若配置了 DB 补偿：按严格条件执行数据库清理
```

长期推动后端修复；短期保证自动化不残留数据。

## 6. 推荐清理契约

### 6.1 单次运行清理

使用本次场景返回的真实 `ticket_id`，不扫描不确定的数据。

筛选条件：

```text
tasks.id = ticket_id
tasks.project_id = Leo_test
tasks.title LIKE 自动化链路验证-%
```

只有全部命中才允许删除。

### 6.2 历史残留清理

历史扫描还必须增加：

```text
tasks.created_by = U1 user id
tasks.created_at <= 本次清理开始时间
```

并支持：

- `--max-age-hours`
- `--ticket-id`
- `--title-prefix`
- `--dry-run` 默认
- `--execute` 显式执行

## 7. 删除顺序

必须在单个事务内按子表到主表顺序删除：

```text
task_comment_read_record
task_comments
task_comment_read
task_dispatch_log
task_operation_logs
task_followers
task_participants
task_spec_doc
tasks
```

每个子表删除都要带上匹配出来的 task_id 白名单。

## 8. Allure 报告设计

清理结果附件：

```json
{
  "api_cleanup": {
    "ticket": "failed: HTTP 500",
    "conversation": "deleted"
  },
  "db_cleanup": {
    "status": "passed",
    "deleted_rows": {
      "task_comments": 3,
      "task_operation_logs": 5,
      "tasks": 1
    }
  },
  "warnings": []
}
```

清理失败只告警，不覆盖业务链路结果。

## 9. 配置与安全

推荐只使用专门的本地/CI 环境变量：

```text
UI_REGRESSION_DB_CLEANUP_ENABLED
UI_REGRESSION_DB_HOST
UI_REGRESSION_DB_PORT
UI_REGRESSION_DB_USER
UI_REGRESSION_DB_PASSWORD
UI_REGRESSION_DB_NAME
```

安全要求：

- 只允许 `helpdesk_test`。
- 拒绝生产库名称和主机。
- 使用最小权限账号。
- 默认 dry-run。
- 输出删除前匹配清单。
- 单事务提交。
- 密码不进入日志和 Allure。

## 10. 实施顺序建议

### 阶段 1：数据库补偿脚本

1. 为现有 CLI 增加自定义安全前缀和项目过滤。
2. 补齐全部 task 子表。
3. 增加本次 `ticket_id` 精确删除模式。
4. 单元测试筛选、删除顺序和 dry-run。
5. 先清理历史残留，再执行一次 UI 回归验证。

### 阶段 2：接入 CleanupManager

1. API 优先。
2. API 失败且 DB 补偿开启时执行数据库兜底。
3. 把 API 和 DB 结果统一写入 Allure。

### 阶段 3：后端修复

1. 向任务模块负责人提供 500 根因和失败工单样例。
2. 后端修复管理员删除 transaction。
3. 自动化保留 DB 兜底一个版本周期，稳定后关闭。

## 11. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 误删真实工单 | P0 | task_id + project_id + title prefix + created_by 四重条件 |
| CI 保存数据库删除权限 | P0 | 专用最小权限账号，仅限 helpdesk_test |
| 表结构变化导致清理遗漏 | P1 | 删除前查询 information_schema 并输出差异告警 |
| API 与 DB 双删重复 | P1 | API 成功后不再执行 DB 补偿 |
| 清理失败掩盖业务结果 | P1 | 清理只告警，不改变 pytest 业务结论 |
| 历史数据范围过大 | P1 | 默认 dry-run，限制时间窗口并输出清单 |

## 12. 待确认事项

1. 是否采用“API 优先 + 数据库补偿兜底”。
2. 是否为自动化提供 `helpdesk_test` 最小权限 DB 删除账号。
3. 是否先清理现有 `812` 之后的历史残留工单。
4. 是否推动后端修复管理员删除 500，并指定负责人和版本。
5. 数据库补偿默认在本地开启、CI 是否同样开启。

以上确认后再进入实现阶段。
