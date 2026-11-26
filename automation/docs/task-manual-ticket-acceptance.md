# 手动单工单验收

在 **Deploy (split build/deploy)** 中选择 `test` 分支、`test` 环境和 `acceptance`，填写一个已转正的工单编号。`git_ref` 留空。工作流从本次 `test` 提交读取用例。

生产凭据只允许在自托管 runner（`self-hosted, ors-verify`）上使用，托管 runner 不持有生产 SSH 私钥。

1. 验证工单编号和转正脚本。生产状态通过云服务器上的只读 API 查询（HTTPS + token，见 `automation/services/ticket-status/`），CI 不再持有 SSH 私钥或数据库口令。
2. 生产状态为 `resolved` 或 `closed` 时，定向作业启用 CI 临时 MySQL 与本地后端并执行该工单的测试脚本。其他已查到的状态跳过定向作业。查库失败、记录缺失和测试失败都使运行失败。
3. 查库日志记录编号、状态和是否符合条件。定向作业日志与 `ticket-gate-results` 提供 pytest 和 JUnit 证据。该模式跳过部署计划和依赖该计划的构建、部署、回滚及报告发布。

验证覆盖状态查询的字段和边界、资格判定，以及部署模式的回归配置。真实工单结果随审批后的 Actions 运行核对。
