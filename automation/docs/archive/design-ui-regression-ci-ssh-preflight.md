# UI 回归 CI SSH 预检与多端口转发设计

> 状态：已确认，按本设计实施
> 日期：2026-09-20
> 范围：仅测试环境，禁止连接生产环境

## 1. 问题现象

PR #112 合并到 `test` 后，UI Regression 工作流可以完成依赖安装、前端构建和 Allure 工具准备，
但执行测试时在 fixture setup 阶段失败。

已确认的失败运行：

- `1f760ad`：`https://github.com/dhualai/OpenRobotService/actions/runs/35486661713`
- 失败步骤：`Run UI regression`
- 失败耗时：约 63 秒
- 结果：`7 errors`
  - 1 条 UI 完整业务链路
  - 6 条真实环境 Smoke

关键日志：

```text
TimeoutError: SSH tunnel did not become ready within 60s
automation/src/remote/ssh_tunnel.py:110
```

完整 traceback 显示：

```text
self.ai_tunnel.start()
```

因此第一条后端转发 `19400 -> 127.0.0.1:9400` 已经启动，失败点位于第二条自动化 AI 转发
`19411 -> 127.0.0.1:9411`。

## 2. 当前实现

当前生命周期：

```text
UiTunnelManager.start()
-> 启动 backend SSH 进程，转发 19400 -> 9400
-> 启动 automation AI SSH 进程，转发 19411 -> 9411
-> conftest 再按需启动 DB SSH 进程，转发 19402 -> 3306
```

已有约束：

- CI 使用独立 `TEST_SSH_PRIVATE_KEY`。
- SSH key 只允许端口转发。
- `authorized_keys` 允许：
  - `127.0.0.1:9400`
  - `127.0.0.1:9411`
  - `127.0.0.1:3306`
- UI 场景和 Smoke 只连接测试环境。

## 3. 根因判断

当前可以确定：

1. 不是业务断言失败。
2. 不是前端构建失败。
3. 不是 Allure 生成失败。
4. 第一条 SSH 转发可以建立。
5. 第二条 SSH 转发在 60 秒内没有让本地监听端口就绪。

当前尚不能确定：

1. 是测试服务器对同一来源的短时间多 SSH 会话限制。
2. 是 CI key 在第二条会话上的认证或 forced-command 行为差异。
3. 是首次 `known_hosts` 写入竞态。
4. 是第二条 SSH 进程仍在握手但被 60 秒超时截断。
5. 是 `9411` 转发是否真正被服务器允许。

现有 `SSHTunnel.start()` 在超时前调用 `self.stop()`，没有保留 stderr，因此日志只能看到超时，
看不到 SSH 客户端正在等待什么。这是当前最主要的可观测性缺口。

## 4. 推荐方案

采用“两小步、一次完成”的方式：

### 4.1 CI 预检

在 pytest 前增加独立预检，输出可定位信息：

1. 校验私钥可以生成公钥，并输出公钥指纹，不输出私钥内容。
2. 校验 runner 能完成到 `125.122.97.107:8802` 的 SSH 握手。
3. 在一次 SSH 会话中同时建立三条本地转发：
   - `19400 -> 127.0.0.1:9400`
   - `19411 -> 127.0.0.1:9411`
   - `19402 -> 127.0.0.1:3306`
4. 检查进程是否退出，并采集脱敏后的 stderr。
5. 检查三个本地端口是否监听。
6. 通过隧道访问：
   - `GET /api/health`
   - `GET /health`
7. 预检失败时立即结束 job，并显示具体失败阶段。

### 4.2 运行时改为单 SSH 多端口转发

不要继续为每个端口启动一个独立 SSH 进程。改为一次 SSH 会话注册多个 `-L`：

```text
ssh ... \
  -L 19400:127.0.0.1:9400 \
  -L 19411:127.0.0.1:9411 \
  -L 19402:127.0.0.1:3306 \
  user@host
```

这样：

- 后端、AI、数据库共用一次认证和一条 TCP 连接。
- 消除第二条、第三条 SSH 会话的竞态。
- 生命周期更简单，退出时只需回收一个进程。
- 任一转发绑定失败时，`ExitOnForwardFailure=yes` 会立即退出，不再静默等待 60 秒。

## 5. 建议改动文件

| 文件 | 变更 |
|---|---|
| `automation/src/remote/ssh_tunnel.py` | 增加多端口转发支持，并在超时/退出时保留脱敏 stderr |
| `automation/src/remote/__init__.py` | 导出新增类型 |
| `automation/src/ui_regression/tunnels.py` | 后端和 AI 改为单 SSH 多端口转发 |
| `automation/tests/ui/conftest.py` | DB cleanup 开启时复用同一条多端口 SSH 会话 |
| `.github/workflows/ui-regression.yml` | pytest 前增加 SSH 预检步骤 |
| `automation/scripts/cli-check-ui-regression-ssh.py` | 新增 CI 可调用的预检入口 |
| `automation/src/remote/tests/test_ssh_tunnel.py` | 覆盖多端口命令和失败诊断 |
| `automation/src/ui_regression/tests/test_tunnels.py` | 覆盖单 SSH 会话生命周期 |
| `automation/docs/UI_REGRESSION_CI.md` | 更新排障流程 |
| `automation/docs/worklog/task-53-ui-regression-ci-ssh-preflight.md` | 完成后记录验证结果 |

## 6. 实施顺序

1. 先实现多端口 SSH 适配器和单元测试。
2. 再改 UI 回归 tunnel manager 与 fixture。
3. 新增 CI 预检入口和 workflow 步骤。
4. 运行自动化框架单测。
5. 在本地进行 Mock 或可用的测试环境验证。
6. 推送功能分支，创建 PR 到 `test`。
7. 合并后由 `push test` 触发真实 CI。
8. 若仍失败，依据预检日志继续定位服务器侧限制。

## 7. 验证标准

- 单元测试可以证明只创建一个 SSH 进程。
- SSH 命令包含三条预期的 `-L`。
- 进程提前退出时错误中包含 SSH stderr。
- 超时时错误中包含最后一段 SSH 诊断，不包含私钥、密码或 token。
- CI 预检能分别报告：
  - key 加载失败
  - SSH 握手失败
  - 本地端口未监听
  - backend health 失败
  - AI health 失败
- 预检通过后，1 条 UI 场景和 6 条 Smoke 可以进入业务执行阶段。
- Allure artifact 仍可生成并下载。

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 单 SSH 进程启动失败会同时影响三条转发 | P1 | 预检先执行，错误阶段清晰，fixture finally 回收 |
| 数据库 cleanup 未开启时不应暴露 3306 转发 | P0 | 仅启用 DB cleanup 时才加入 3306 |
| stderr 可能包含主机、用户名或命令路径 | P1 | 只输出最后一段必要错误，移除 key path、密码和 token |
| 服务器仍限制同一连接的多端口转发 | P1 | 预检会直接定位到具体转发，再调整服务器 key 配置 |
| 修改 shared SSH adapter 影响真实测试 | P1 | 保留原单端口 API，新增多端口能力并补充回归测试 |

## 9. 待确认

1. 是否接受“一次 SSH 会话承载 backend + AI + DB 三条 `-L`”的实现方式。
2. 是否接受 CI 在 pytest 前额外运行一次 SSH 预检。
3. 是否接受本次只修 CI 环境连接，不改业务测试的断言和步骤。
4. ORS 工单号是否使用新的工单；如果沿用，请提供工单号。

确认后进入实现阶段。
