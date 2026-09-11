# Task 32：业务链路 CI 访问与安全边界

> 日期：2026-09-11
> 状态：环境侧配置完成，GitHub Secrets 已添加，待手动触发连通性检查

## 目标

为“摇人问答 -> 提单 -> 派单 -> 处理 -> 关闭”真实后端业务链路的 CI 运行准备安全访问方式。

## 已完成

1. 确认 CI 安全边界：
   - PR 仅运行 Mock/无 secret 测试。
   - 真实后端链路仅允许 `push` 到 `develop` 和 `workflow_dispatch`。
2. 创建并验证测试账号：
   - `u1_auto` / 自动化提单用户
   - `u2_auto` / 自动化处理人
3. 生成 GitHub Actions 专用 SSH key，不使用个人密钥。
4. 将专用公钥加入测试服务器 `authorized_keys`。
5. 将专用 key 限制为仅允许端口转发到 `127.0.0.1:9400`，并加入强制命令阻止远程命令执行。
6. 验证受限后仍能通过 SSH 隧道访问测试后端 `/api/health`。
7. 新增手动触发的 `.github/workflows/real-access-check.yml`，用于在 GitHub Actions 中验证同一访问链路。
8. GitHub repository secrets 已添加：`TEST_SSH_PRIVATE_KEY`、`REAL_U1_PASSWORD`、`REAL_U2_PASSWORD`。

## CI 检查范围与方式

- 访问层：GitHub runner -> SSH 8802 -> `127.0.0.1:9400` -> `GET /api/health`。
- 业务链路层：登录 U1/U2 -> 摇人问答 -> 提单 -> 派单 -> 处理 -> 已解决 -> 关闭。
- 环境：仅 `TestOpenRobotService`（9400 / 9401 / `helpdesk_test`），不包含 8400/8401 实例。
- 访问层手动触发；业务链路层使用 `push develop` + `workflow_dispatch`。
- 第一版不配置定时任务；PR 不接触 secrets。

## 修改文件

- `automation/docs/design-business-chain-refactor.md`
- `automation/docs/business-flows/real-env-inventory-u1u2.md`
- `.github/workflows/real-access-check.yml`
- `automation/docs/ci-automation-loop.md`
- `automation/docs/worklog/task-32-business-chain-ci-access.md`

## 验证

- 受限 SSH key 建立端口转发后，`GET /api/health` 返回 `healthy`。
- 带远程命令的 SSH 连接不会执行用户命令。
- 测试账号可登录，并已绑定 `Leo_test` 的 `role_ef0c8cf7`。
- `real-access-check.yml` 已通过本地 YAML 语法校验；尚未在 GitHub Actions 中手动触发。

## 风险

- GitHub Secrets 已添加但尚未通过 Actions 实测，密钥内容错误或网络不可达时会在访问层失败。
- 可控 AI 回复方案未确认，真实链路暂不能稳定走到提单。
- 自动清理接口和权限未确认。

## 下一步

1. 手动触发 `real-access-check.yml`，确认 GitHub 托管 runner 可访问真实后端。
2. 确认可控 AI 回复实现方式。
3. 进入真实后端业务链路 workflow 与执行层设计。
