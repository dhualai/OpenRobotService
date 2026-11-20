# Task 50：测试数据数据库补偿清理

> 日期：2026-09-19
> 状态：实现完成，历史清理和自动兜底均已实测

## 目标

在管理员删除接口返回 500 时，为 UI 回归提供严格的数据库补偿清理：

```text
API 删除优先
-> API 失败
-> 数据库补偿精确匹配本次 ticket_id
-> 单事务删除任务及全部已知子表
-> 清理结果写入 Allure
```

## 修改文件

- `automation/src/ui_regression/db_cleanup.py`
- `automation/src/ui_regression/tests/test_db_cleanup.py`
- `automation/src/ui_regression/cleanup.py`
- `automation/src/ui_regression/tests/test_cleanup.py`
- `automation/tests/ui/conftest.py`
- `automation/tests/ui/test_call_qa_to_ticket_close_regression.py`
- `automation/scripts/cli-cleanup-real-test-data.py`
- `automation/scripts/run-ui-regression.ps1`
- `automation/config/ui_regression.local.yaml`
- `automation/docs/UI_REGRESSION.md`

## 实现内容

1. 新增 `DatabaseCleanup`：
   - 只允许测试数据库。
   - 必须提供 `project_id`。
   - 必须提供精确 `ticket_id` 或安全标题前缀。
   - 默认 dry-run。
   - 单事务删除全部已知 task 子表和主表。
2. 子表顺序：
   - `task_comment_read_record`
   - `task_comments`
   - `task_comment_read`
   - `task_dispatch_log`
   - `task_operation_logs`
   - `task_followers`
   - `task_participants`
   - `task_spec_doc`
   - `tasks`
3. `CleanupManager` 增加 API 失败后的数据库兜底。
4. UI runtime 在显式开启时建立独立 MySQL SSH 隧道。
5. 一键脚本支持：
   - `-EnableDbCleanup`
   - `-DbCleanupUser`
   - `-DbCleanupPassword`
   - `-DbCleanupDatabase`
6. 重写 CLI 清理脚本：
   - 支持 `--ticket-id`
   - 支持自定义安全前缀
   - 支持 `--project-id`
   - 支持 `--created-by`
   - 默认 dry-run
   - 必须显式 `--execute`

## 验证结果

数据库清理和 CleanupManager 单测：

```text
21 passed in 10.33s
```

覆盖：

- 非测试数据库拒绝。
- 缺少精确筛选条件拒绝。
- 安全条件 SQL 拼装。
- 子表先于 tasks 删除。
- dry-run 不执行 DELETE。
- API 失败后调用数据库补偿。
- 数据库补偿失败只产生告警。

## 真实库验证

创建 MySQL 最小权限账号：

```text
username: automation_cleanup
host: 127.0.0.1
database: helpdesk_test
privileges: SELECT, DELETE
scope: 9 张任务相关表
users 表访问: denied
```

历史 dry-run：

```text
Matched 35 test ticket(s)
IDs: 812-846
```

历史实际清理：

```text
task_comment_read_record: 366
task_comments: 207
task_comment_read: 61
task_dispatch_log: 35
task_operation_logs: 371
task_followers: 0
task_participants: 0
task_spec_doc: 0
tasks: 35
```

自动兜底验证：

```text
UI 回归: 7 passed in 24.31s
工单 848:
  API 删除: HTTP 500
  数据库补偿: tasks=1
  报告清理结果: 工单(数据库补偿)、会话
```

默认启用后再次验证：

```text
DB cleanup prefix: 自动化链路验证-%
UI 回归: 7 passed in 25.61s
工单 849:
  数据库补偿: tasks=1
  报告 warning: []
```

最终 dry-run：

```text
Matched 0 test ticket(s)
```

## 风险

- 数据库补偿仍应保持 API 优先，不能跳过产品接口直接删除。
- 最小权限账号密码保存在本机用户环境变量中，不进入仓库。
- 后端管理员删除接口返回 500 的根因仍需后端负责人修复。

## 下一步

1. 将数据库补偿密码配置到 GitHub Secret，供后续 CI 使用。
2. 推动后端修复管理员删除 500。
3. 进入 CI 接入阶段。
