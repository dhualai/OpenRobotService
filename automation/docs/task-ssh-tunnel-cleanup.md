# 任务记录：SSH 隧道式 CI 接入的下线与文档收口

> 日期：2026-09-22
> ORS：待补（本次为安全收口类改动）
> 状态：仓库内改动已完成；生产分支的落地待人工推送

## 背景

UI 回归最初采用「GitHub 托管 runner + 受限 SSH key 端口转发」访问测试环境。
该方案存在三个问题：

1. 需要在测试服务器 `authorized_keys` 中保留一把 CI 公钥，形成长期登录凭据；
2. 需要在 GitHub Secrets 中保存对应私钥（`TEST_SSH_PRIVATE_KEY`），
   并在 CI 运行时落盘到 runner；
3. 调试期间产生了 `debug-ui-regression-ssh.yml` 与 `ssh-debug-log` artifact，
   对外暴露了网络与主机诊断信息。

改为 **self-hosted runner 直连** 后，runner 与后端/AI/数据库同处测试服务器本机，
测试代码直接访问 `127.0.0.1:9400` / `127.0.0.1:9411`，上述三条全部不再需要。

## 环境侧已完成（由人工执行，非仓库改动）

- 删除 GitHub Secret `TEST_SSH_PRIVATE_KEY`（原仓库与 fork）。
- 删除调试分支 `automation-fix-ui-regression-ci-ai-tunnel-debug`。
- 测试服务器 `usp-a` 的 `authorized_keys` 中已无 `openrobot-ci-actions` 及
  任何带 `command=` / `restrict` / `permitopen` 的条目。

## 仓库侧改动

| 文件 | 改动 |
|---|---|
| `.github/workflows/real-access-check.yml` | **删除**。该 workflow 硬编码测试服务器地址/端口/账号并依赖已删除的私钥 Secret |
| `automation/docs/UI_REGRESSION_CI.md` | 顶部加废弃说明，第 4 节「SSH 白名单」标注已废弃 |
| `automation/docs/business-flows/real-env-inventory-u1u2.md` | 状态行更新，2.1 / 2.2 / 5.1 / 5.2 标注废弃并指向 self-hosted 直连 |
| `automation/docs/worklog/task-54-ui-regression-ci-health-retry.md` | 加废弃说明，标注 CI key 作废 |
| `automation/docs/archive/worklog/task-51-ui-regression-ci.md` | 加历史归档提示 |

## 未改动（有意保留）

- `automation/src/remote/ssh_tunnel.py`、`automation/src/ui_regression/tunnels.py`：
  隧道能力本身作为「远程环境访达」的通用组件保留，默认不启用（`UI_REGRESSION_DIRECT=1`）。
  其中不含任何硬编码凭据。
- `archive/` 下的历史设计稿：属于归档，不再追改。

## 验证

- 仓库内已无 `real-access-check` 的现行引用（仅历史归档与已标注废弃的文档提及）。
- self-hosted 直连路径已实测通过：UI Regression #17，7 个用例全部通过，
  Allure 报告含 17 步场景与 6 条 Smoke。

## 风险

- `origin/dev`（生产分支）与 `origin/main` 上仍存在 `real-access-check.yml`，
  需在对应分支单独删除；`origin/dev` 还落后 `origin/test` 113 个提交。
- self-hosted runner 会在服务器上执行 workflow 中的任意命令，触发来源必须持续限制在
  受信分支 push 与 `workflow_dispatch`，禁止 fork PR。

## 下一步

1. 在 `test` 分支删除 `real-access-check.yml`，并随下次 `test -> dev` 合并带入生产分支。
2. 在 `dev` 分支单独删除该文件（若短期不合并 `test`）。
3. 手动触发一次 UI Regression，确认 runner 可正常接单。
4. 评估将 MySQL 3306 由 `0.0.0.0` 改为仅监听 `127.0.0.1`。
