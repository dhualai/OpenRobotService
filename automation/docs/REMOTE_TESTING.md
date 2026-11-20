# 真实环境 SSH 测试

真实环境通过 SSH 本地端口转发访问，不在仓库保存网络凭证。

## 环境变量

```text
REAL_SSH_HOST
REAL_SSH_PORT
REAL_SSH_USER
REAL_SSH_KEY
REAL_REMOTE_API_HOST=127.0.0.1
REAL_REMOTE_API_PORT=9400
REAL_LOCAL_API_PORT=0
REAL_SSH_CONNECT_TIMEOUT=20
```

## 运行

```powershell
python automation/scripts/cli-real-suite.py `
  --scenario real_smoke `
  --env test
```

生命周期链路：

```powershell
python automation/scripts/cli-real-suite.py `
  --scenario real_lifecycle `
  --env test
```

生命周期用例需要：

```text
REAL_U1_USERNAME / REAL_U1_PASSWORD
REAL_U2_USERNAME / REAL_U2_PASSWORD
```

## 约束

- SSH  tunnel 只转发 API 端口；数据库地址仍由目标环境的 `config.yaml` 或专用环境配置决定。
- 真实环境测试必须使用唯一标题前缀，并在 teardown 中清理测试数据。
- 凭据只来自环境变量或 CI Secrets，不写入 Trace、日志或仓库。
- 环境不可达时先运行 `diagnose_environment`，不要反复重跑业务用例。
## DB assertions

真实生命周期默认开启 MySQL 断言。若只想验证 API 链路、暂时没有数据库隧道：

```powershell
$env:REAL_DB_ASSERTIONS = "0"
python automation/scripts/cli-real-suite.py --scenario real_lifecycle --env test
```

有数据库访问条件时保持默认 `1`，并在 schema、状态流转和关闭阶段校验 MySQL。