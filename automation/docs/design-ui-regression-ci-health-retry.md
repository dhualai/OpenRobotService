# UI 回归 CI 健康检查重试设计

> 状态：已确认实施
> ORS：877
> 日期：2026-09-20
> 范围：仅 GitHub Actions 预检脚本，不修改业务服务

## 1. 问题

UI Regression #5 连续多次在 `Preflight real environment SSH forwards` 失败，但失败阶段不稳定：

- 有时 `Backend health: OK` 后 AI health 超时。
- 有时 backend health 本身超时。
- 测试服务器侧已验证 backend `9400`、自动化 AI `9411` 均正常。
- 使用受限调试 key 从本机建立单会话多端口转发后，`9411/health` 返回 `200`。

结论：SSH 多端口转发和测试环境服务正常，GitHub runner 的首轮 health 请求存在瞬时超时。

## 2. 当前实现

`automation/scripts/cli-check-ui-regression-ssh.py`：

```python
def _check_http(url: str) -> None:
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
```

问题：

1. 单次请求超时即判定环境失败。
2. 日志只有最终异常，无法区分 backend 和 AI 的失败阶段。
3. `httpx` 默认读取环境代理，runner 代理配置可能干扰本地转发。

## 3. 设计

仅修改预检脚本和对应单测。

### 3.1 HTTP 健康检查

新增参数：

```text
timeout=30s
attempts=3
retry_delay=2s
trust_env=False
```

每次尝试打印：

```text
Backend health: attempt 1/3
Automation AI health: attempt 1/3
```

全部失败后抛出包含阶段名和最后一次错误的异常。

### 3.2 数据库端口检查

同样采用 3 次尝试和 2 秒间隔，打印：

```text
Database forward: attempt 1/3
```

### 3.3 安全边界

- 不输出密码、token 或私钥。
- 不改变 SSH 转发范围。
- 不修改 backend、AI、frontend 业务逻辑。
- 不增加生产环境连接。

## 4. 文件计划

| 文件 | 变更 |
|---|---|
| `automation/scripts/cli-check-ui-regression-ssh.py` | 增加阶段日志、超时重试、代理隔离 |
| `automation/scripts/tests/test_cli_check_ui_regression_ssh.py` | 覆盖成功重试、最终失败和 TCP 重试 |
| `automation/docs/UI_REGRESSION_CI.md` | 更新排障说明 |
| `automation/docs/worklog/task-54-ui-regression-ci-health-retry.md` | 完成后记录结果 |

## 5. 验证

1. 单元测试证明失败后可以重试成功。
2. 单元测试证明超过次数后保留阶段信息。
3. YAML 和 Python 编译检查通过。
4. 合并到 `test` 后重跑 UI Regression，确认预检通过并进入 UI + Smoke。
