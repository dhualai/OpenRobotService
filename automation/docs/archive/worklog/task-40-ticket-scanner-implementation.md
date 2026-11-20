# Task 40：生产工单只读扫描器

> 日期：2026-09-15
> 状态：第一模块已实现并验证

## 目标

实现工单驱动测试流水线的第一模块：只读扫描生产 `Leo_test` 项目中带 `auto_case` 标签的新建 feature/bug 工单。

## 实现内容

- 新增 `TicketCandidate` 数据模型。
- 新增 `TicketScanner` 过滤逻辑：
  - `project_id = 'Leo_test'`
  - `status = new`
  - `task_type in (feature, bug)`
  - `tags` 包含 `auto_case`
  - 支持排除机器人创建人
- 新增 `SSHMySQLTicketSource`：
  - 使用现有 SSH 建立临时 MySQL 隧道。
  - 只执行参数化 SELECT。
  - 查询结束后关闭连接和隧道。
- 新增 CLI：
  - `automation/scripts/cli-scan-ticket-candidates.py`

## 修改文件

- `automation/src/ticket_pipeline/__init__.py`
- `automation/src/ticket_pipeline/models.py`
- `automation/src/ticket_pipeline/scanner.py`
- `automation/src/ticket_pipeline/tests/__init__.py`
- `automation/src/ticket_pipeline/tests/test_scanner.py`
- `automation/scripts/cli-scan-ticket-candidates.py`

## 验证结果

单元测试：

```text
2 passed in 0.32s
```

生产只读扫描（工单 837 打上 `auto_case` 后）：

```json
{
  "project_id": "Leo_test",
  "status": "new",
  "task_types": ["feature", "bug"],
  "tag": "auto_case",
  "count": 1,
  "tickets": [
    {
      "id": 837,
      "task_type": "feature",
      "status": "new"
    }
  ]
}
```

工单 837 的标签已从 `["ai_generated"]` 更新为
`["ai_generated", "auto_case"]`，扫描器已确认可识别。

CLI 现在默认把完整候选 JSON 写入：

```text
automation/output/ticket-candidates.json
```

避免 Windows 控制台中文乱码。

## 风险

- 扫描当前依赖现有 SSH 和 MySQL 账号，后续 CI 使用需要确认最小权限和网络可达性。
- 标签 `auto_case` 需要业务侧开始给目标工单打标后才会产生候选。

## 下一步

1. 确认并发布 `auto_case` 标签使用规范。
2. 实现“候选工单 -> AI 生成候选用例”。
3. 接入 GitHub PR review 和正式用例归档。
