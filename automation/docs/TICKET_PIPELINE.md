# 工单驱动测试流水线

## 阶段

```text
生产工单扫描
  -> AI 生成候选功能用例
  -> GitHub PR 人工 Review
  -> manifest.review_status=approved
  -> promote 到正式引用目录
  -> test 分支执行工单用例 + 回归用例
```

## CLI

```powershell
# 扫描生产候选工单
python automation/scripts/cli-ticket-pipeline.py scan --limit 20

# 单工单生成
python automation/scripts/cli-ticket-pipeline.py generate `
  --ticket-id 837 --title "..." --description "..."

# 扫描并批量生成
python automation/scripts/cli-ticket-pipeline.py run --limit 20

# Review 后 promotion
python automation/scripts/cli-ticket-pipeline.py promote --ticket-id 837

# 根据 commit/PR 文本选择要跑的用例
python automation/scripts/cli-ticket-pipeline.py select `
  --text "fix ORS-837" `
  --regression tests/business_chain `
  --regression tests/tasks
```

## 约束

- 生产数据库只读。
- 只在人工 Review 通过后 promotion。
- AI 生成产物不能自动合入正式套件。
- 生成和 promotion 产物必须保留工单号、来源和 manifest。
## CI Secrets

`ticket-pipeline.yml` 使用以下 GitHub Secrets：

```text
PROD_TICKET_SSH_HOST
PROD_TICKET_SSH_PORT
PROD_TICKET_SSH_USER
PROD_TICKET_SSH_PRIVATE_KEY
PROD_TICKET_DB_PASSWORD
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

workflow 会把 `PROD_TICKET_SSH_PRIVATE_KEY` 写入 runner 临时文件，
再通过 `PROD_TICKET_SSH_KEY` 路径传给扫描器。