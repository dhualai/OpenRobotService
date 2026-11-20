# 工单自动化闭环 review 与收口

日期：2026-10-22

## 目标

对已有的工单自动化闭环改动做代码 review，修复阻塞 CI 的问题，完成可执行的本地验证，
并把改动从 ORS-909 的 UI 回归分支中剥离。

## 本次修复

1. `ticket-gate.yml` checkout 改为 `fetch-depth: 0`，避免浅克隆时无法解析
   `github.event.before..github.sha`，导致多 commit 工单号提取步骤失败。
2. 生产工单只读凭据不完整时，CI 显式设置 `TICKET_STATUS_LOOKUP_DISABLED=1`，
   门禁不再尝试访问默认生产库，直接按 fail-closed 跳过功能用例。
3. 删除工单用例生成器末尾的一段不可达重复代码。
4. 修复 `cli-ticket-pipeline.py generate --project-name` 的默认值乱码。
5. 补充 `TICKET_LOOP.md` 的文档入口，并增加缺凭据条件下的 CLI 回归测试。

## 产物

- 分支：`codex/ticket-loop-pipeline`
- 提交：`a4cd7b9d feature（automation）：补全工单自动化闭环的生成、门禁与通知`
- 闭环说明：`automation/docs/TICKET_LOOP.md`
- 门禁说明：`automation/docs/TICKET_GATE.md`

## 验证结果

- `pytest src config ci_ai_gen mcp_server -q`：378 passed，1 skipped
  （本机未安装 `mcp`，与本次改动无关）
- `pytest src/ticket_pipeline/tests ci_ai_gen/tests -q`：130 passed
- `pytest tests/tasks tests/auth tests/admin tests/call tests/business_chain -q`：131 passed
- CLI fail-closed 冒烟：工单状态不可读时跳过功能用例，`tests/tasks` 回归 32 passed，
  门禁正常退出

## 未覆盖与下一步

- 本机没有生产库只读凭据和 LLM 密钥，未执行真实工单扫描、用例生成和真实后端功能用例。
- 尚未推送分支或创建 PR。
- 真实环境验收前需配置 `PROD_TICKET_*`、`LLM_*`、`SEED_BOT_*`、`TICKET_COMMENT_*`
  Secrets/Variables，并开启 `TICKET_PIPELINE_ENABLED`、`TICKET_GATE_ENABLED`。
- 验收时应选择一个 `resolved` / `closed` 的 feature 工单，完整走一遍
  扫描 → 生成 → 人工 review → promotion → test 分支门禁。
