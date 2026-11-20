# Task 53：UI Regression CI SSH 预检与多端口转发

> 日期：2026-09-20
> ORS：869
> 状态：实现完成，本地验证通过，待 GitHub Actions 验证

## 目标

修复 UI Regression CI 在第二条 SSH 隧道 `19411 -> 9411` 上等待 60 秒后超时的问题。

## 问题证据

PR #112 合并后的 UI Regression #3：

```text
TimeoutError: SSH tunnel did not become ready within 60s
self.ai_tunnel.start()
automation/src/remote/ssh_tunnel.py:110
```

7 条 UI/Smoke 用例均在 fixture setup 阶段报错，不是业务断言失败。

## 修改文件

- `.github/workflows/ui-regression.yml`
- `automation/scripts/cli-check-ui-regression-ssh.py`
- `automation/src/remote/ssh_tunnel.py`
- `automation/src/remote/__init__.py`
- `automation/src/remote/tests/test_ssh_tunnel.py`
- `automation/src/ui_regression/config.py`
- `automation/src/ui_regression/tunnels.py`
- `automation/src/ui_regression/tests/test_tunnels.py`
- `automation/tests/ui/conftest.py`
- `automation/docs/UI_REGRESSION.md`
- `automation/docs/UI_REGRESSION_CI.md`
- `automation/docs/design-ui-regression-ci-ssh-preflight.md`

## 实现内容

1. `SSHTunnel` 支持一次 SSH 进程注册多个 `-L` 转发。
2. backend、automation AI 和可选 MySQL 转发合并到同一条 SSH 会话。
3. SSH 提前退出或超时时保留脱敏 stderr，包含退出码和本地端口列表。
4. 新增 CI 预检脚本：
   - 输出 SSH 公钥指纹。
   - 建立全部转发。
   - 检查 backend `/api/health`。
   - 检查 automation AI `/health`。
   - 检查 MySQL 本地转发端口。
5. workflow 在构建前端前执行预检，尽早暴露环境失败。
6. DB cleanup 未开启时不注册 3306 转发。

## 本地验证

```text
automation/src/remote/tests + automation/src/ui_regression/tests
27 passed in 17.48s
```

```text
automation/tests/ui --collect-only
10 tests collected
```

## 风险

- 真实 CI key 的服务器行为仍需通过 `push test` 验证。
- 单 SSH 会话中任一 `-L` 绑定失败会让整条会话退出，这是预期行为，由预检明确报告阶段。
- 当前尚未在本地复现 CI key 的多会话限制，因此不把根因结论写成服务器侧已确诊。

## 下一步

1. 推送功能分支。
2. 创建到 `test` 的 PR，使用 merge commit。
3. 合并后观察 `Preflight real environment SSH forwards`。
4. 预检通过后确认 UI 场景和 6 条 Smoke 进入业务执行阶段。
