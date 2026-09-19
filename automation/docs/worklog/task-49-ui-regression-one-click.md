# Task 49：本地 UI 回归一键运行

> 日期：2026-09-19
> 状态：实现完成，默认一键路径验证通过

## 目标

提供一条 PowerShell 命令，从干净终端自动完成：

```text
前端正式构建
-> SSH 隧道和本地 Gateway
-> UI 完整业务链路
-> 真实测试环境只读 Smoke
-> 联合 Allure HTML
-> 本地报告服务
```

## 修改文件

- `automation/scripts/run-ui-regression.ps1`
- `automation/config/ui_regression.local.yaml`
- `automation/docs/UI_REGRESSION.md`

## 实现内容

1. 新增 `run-ui-regression.ps1`：
   - 解析 YAML 非敏感配置。
   - 支持参数、环境变量和交互式密码输入。
   - 默认构建前端正式产物。
   - 执行 UI 场景和 6 条真实环境 Smoke。
   - 失败时仍尽量生成 Allure。
   - 启动隐藏的本地报告 HTTP 服务并输出 URL。
2. 新增 `ui_regression.local.yaml`：
   - SSH、端口、测试账号名、报告目录和超时配置。
   - 不保存密码。
3. 新增 `UI_REGRESSION.md`：
   - 前置条件。
   - 一键命令。
   - 跳过构建、关闭浏览器打开等参数。
   - 报告结构、安全边界和失败处理。
4. 增加空闲端口自动探测：
   - 先尝试连接目标端口。
   - 再进行本地绑定校验。
   - 端口冲突时自动选择后续空闲端口。
5. 将 SSH 隧道超时提高到 60 秒，降低测试服务器网络抖动影响。
6. 增加受管报告服务：
   - 记录 PID、启动时间、端口和报告目录。
   - 连续运行复用同一服务，保持固定端口。
   - 服务异常或配置变化时回收旧实例。
7. 使用进程启动时间 `Ticks` 校验受管 PID，避免 Windows 时区字符串解析偏差。

## 验证结果

固定端口连续运行两次：

```text
7 passed in 33.45s
Report: http://127.0.0.1:8086/
Report server PID: 16012

7 passed in 33.86s
Report: http://127.0.0.1:8086/
Report server PID: 16012
```

默认完整一键路径：

```text
vite build: built in 37.03s
7 passed in 24.67s
Report: http://127.0.0.1:8089/
```

验证覆盖：

- YAML 配置解析成功。
- PowerShell 脚本语法检查通过。
- 前端正式构建成功。
- SSH 隧道和本地 Gateway 自动启动。
- UI 场景和 Smoke 联合执行成功。
- 联合 Allure 自动生成。
- 报告服务自动启动并返回 HTTP 200。
- 连续运行时复用受管报告服务，端口和 PID 保持固定。
- 报告端口被非本脚本进程占用时才自动切换。

## 风险

- 脚本默认每次构建前端，完整运行约需要 1-2 分钟；开发调试可使用
  `-SkipFrontendBuild`。
- 受管报告状态文件位于 `automation/output/ui-regression-report-server.json`，属于本地产物。
- 管理员删除工单接口仍返回 500，清理残留工单的问题尚未解决。

## 下一步

1. 获取一键运行任务的 ORS 工单号。
2. 提交并推送当前功能分支。
3. 决定是否接入 `test` 分支 CI 和报告链接评论。
