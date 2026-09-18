# Ticket Gate（test 分支门禁）

`ticket-gate.yml` 在代码合并到 `test` 分支时执行：

1. 从 commit message 提取工单号。
2. 查找已 promotion 的工单用例。
3. 拼接回归用例。
4. 执行 pytest。
5. 上传 JUnit 和 Allure 结果。

## 本地运行

```powershell
python automation/scripts/cli-ticket-gate.py `
  --message-file commit-message.txt `
  --regression tests/business_chain `
  --regression tests/tasks `
  --env local
```

## 工单号格式

支持：

- `ORS-837`
- `#838`
- `工单:839`
- `ticket_id: 840`

## 门禁规则

- 没有工单号时只跑回归用例。
- 有工单号但未 promotion 时，不自动伪造工单用例。
- pytest 失败即门禁失败。
- 真实环境测试需要 `REAL_API_BASE_URL`、`REAL_U1_*`、`REAL_U2_*` 等配置。