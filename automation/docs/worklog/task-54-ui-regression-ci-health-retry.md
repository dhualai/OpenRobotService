# Task 54：UI Regression CI 健康检查重试

> **后续变更（2026-09-22）**：本文针对的「GitHub 托管 runner + 受限 SSH key 隧道」链路
> 已停用，改为 self-hosted runner 直连。下文提到的 `TEST_SSH_PRIVATE_KEY`、
> `permitopen`、调试 key 等均已作废并清理，仅作历史记录。

> 日期：2026-09-20
> ORS：877
> 状态：实现完成，本地验证通过，待 GitHub Actions 验证

## 目标

修复 UI Regression 预检在测试环境服务正常时，仍因首轮 HTTP 健康检查瞬时超时而失败的问题。

## 已验证事实

- 测试后端 `9400/api/health` 返回 `200`。
- 自动化 AI `9411/health` 返回 `200`。
- ~~CI key 已配置 `permitopen="127.0.0.1:9411"`。~~（该 key 已作废）
- 使用受限调试 key 从本机建立单会话多端口转发后，AI health 返回 `200`。
- GitHub Actions 连续多次在 backend 或 AI 首轮 health 请求上超时。

## 修改文件

- `automation/scripts/cli-check-ui-regression-ssh.py`
- `automation/scripts/tests/test_cli_check_ui_regression_ssh.py`
- `automation/docs/UI_REGRESSION_CI.md`
- `automation/docs/design-ui-regression-ci-health-retry.md`
- `automation/docs/worklog/task-54-ui-regression-ci-health-retry.md`

## 实现内容

1. HTTP 健康检查超时由 10 秒提高到 30 秒。
2. backend、AI 和数据库端口检查最多尝试 3 次。
3. 每次尝试之间等待 2 秒。
4. HTTP 客户端禁用环境代理：`trust_env=False`。
5. 日志打印阶段名和尝试次数：
   - `Backend health: attempt 1/3`
   - `Automation AI health: attempt 1/3`
   - `Database forward: attempt 1/3`
6. 最终失败时保留阶段名和最后一次错误。

## 本地验证

```text
automation/scripts/tests/test_cli_check_ui_regression_ssh.py
3 passed in 1.49s
```

完整 `automation/scripts/tests` 中，新增测试通过；其余历史工具测试有 6 个因仓库缺少
`automation/testdata/cases/api-test-cases.xlsx` 报错，与本次修改无关。

## 风险

- 如果测试环境连续三次都不可达，预检仍会失败，这是预期行为。
- 预检最坏耗时增加，但 job 总 timeout 为 30 分钟，余量足够。

## 下一步

1. 推送功能分支并创建 PR 到 `test`。
2. 合并后重跑 UI Regression。
3. 确认预检通过并进入 UI 场景 + 6 条 Smoke。
