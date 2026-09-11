# 测试环境盘点与 U1/U2 创建参数

> 状态：`u1_auto`/`u2_auto` 已创建并可登录；GitHub Actions 专用 key 已加固；Secrets 已配置待实测；可控回复待确认
> 日期：2026-09-09
> 目标环境：真实后端 `TestOpenRobotService`

## 1. 环境连接信息

| 项目 | 值 |
|------|----|
| 服务器 | `125.122.97.107` |
| SSH 端口 | `8802` |
| SSH 用户 | `usp-a` |
| 测试代码目录 | `/data/apps/TestOpenRobotService` |
| 后端 API | `9400`，`/docs` 与 `/api/health` 均正常 |
| AI 服务 | `9401` |
| 另一个实例 | `/data/apps/OpenRobotService`：后端 `8400` + AI `8401`，不作为本方案目标 |

已确认本机公钥在测试服务器 `authorized_keys` 中，SSH 可直接登录。

## 2. 外网可达性

从本机访问 `http://125.122.97.107:9400/api/health` 当前不可达（连接失败）。

GitHub Actions 若使用 GitHub 托管 runner，不能直接请求 `9400`，需要先确认二选一：

1. 在测试服务器配置自托管 GitHub Actions runner。
2. GitHub Actions 先 SSH 登录 `125.122.97.107:8802`，再通过端口转发访问 `localhost:9400`。

已确认采用方案 2（SSH 端口转发）：

- GitHub Actions 使用 GitHub 托管 runner。
- 测试前建立 SSH 隧道：`125.122.97.107:8802` -> `localhost:9400`。
- 后端访问地址仍使用 `http://localhost:9400`，实际经隧道转发到测试环境后端。

### 2.1 GitHub Actions 专用 SSH key

- 已生成独立密钥对，不使用个人 SSH 私钥。
- 公钥已加入测试服务器 `usp-a` 的 `authorized_keys`。
- key 已限制为 `restrict,port-forwarding,permitopen="127.0.0.1:9400"`。
- 同时加入强制命令，使该 key 只能用于无命令的端口转发，不能执行远程命令。
- 已验证：可以建立隧道并访问 `/api/health`；带命令的 SSH 连接不会执行用户命令。
- 私钥已配置到 GitHub repository secret `TEST_SSH_PRIVATE_KEY`，尚待在 GitHub Actions 中实测。

### 2.2 CI 连通性检查范围与触发方式

第一版检查分两层：

1. 访问层（`real-access-check.yml`）：
   - GitHub 托管 runner -> `125.122.97.107:8802`。
   - 专用 SSH key 鉴权 -> SSH 隧道 -> `127.0.0.1:9400`。
   - 只调用 `GET /api/health`，验证网络、密钥、隧道和后端进程是否可用。
2. 业务链路层（待实现）：
   - GitHub 托管 runner -> SSH 隧道 -> 后端 9400。
   - U1/U2 登录、摇人问答、转工单、自动派单、处理、已解决、关闭。
   - 间接覆盖后端依赖的 `helpdesk_test`、Redis、AI 服务 9401 和派单 Worker。

环境范围：

- 只检查 `TestOpenRobotService`：后端 9400、AI 服务 9401、测试库 `helpdesk_test`。
- 不检查另一个实例 `/data/apps/OpenRobotService` 的 8400/8401。
- 不使用真实项目 `001`，测试数据只在 `Leo_test` 范围内产生。

触发方式：

- 访问层：默认使用 `workflow_dispatch` 手动触发；在 feature 分支验证期间临时增加分支 `push` 触发，验证通过后移除。
- 业务链路层：只允许 `push develop` 和 `workflow_dispatch`；访问层作为业务链路执行的前置检查。
- 定时任务：第一版不启用；链路稳定后再评估是否增加每日巡检。
- PR：只跑 Mock/无 secret 测试，不连接真实后端。

## 3. 测试库盘点结果

### 角色

| 角色 ID | 名称 | 类型 |
|---------|------|------|
| `role_ef0c8cf7` | 普通用户 | project |
| `role_4e34cf1c` | 实施 | project |
| `role_d4004bea` | 调度研发 | project |
| `role_e8b90213` | 部门负责人 | project |
| `role_1322f3d3` | 项目经理 | project |
| `user` | 用户 | system |
| `role_16177e72` | 开发者 | system |
| `role_6319024e` | 超级管理员 | system |

### 项目

| 项目 ID | 名称 | 说明 |
|---------|------|------|
| `Leo_test` | 摇人吧服务号-测试 | 现有测试项目，当前成员均标记为测试/小号 |
| `001` | 摇人吧服务号 | 真实业务项目，成员较多，不建议直接用 |

建议测试账号挂在 `Leo_test`，避免污染 `001` 的真实成员和派单数据。

### 工单阶段模板

| 类型 | 阶段数 | 阶段名称 |
|------|--------|---------|
| problem | 3 | 初步诊断 / 临时解决 / 最终解决 |
| bug | 4 | 问题确认 / 修复方案 / 完成修复 / 验证修复 |
| feature | 8 | 需求澄清 / 评审 / 排期 / 设计 / 开发 / 测试 / 验收 / 发布 |
| support | 3 | 初步响应 / 现场/远程支持 / 问题解决 |
| other | 4 | 初步响应 / 问题澄清 / 解决方案 / 问题解决 |

环境没有单阶段工单类型，第一版业务链路固定使用 `problem`，后续测试脚本完整推进
初步诊断 / 临时解决 / 最终解决 3 个阶段。

## 4. U1/U2 创建参数

密码由团队指定，不写入本文档和代码仓库，后续放入 GitHub Secrets。

| 字段 | U1 | U2 |
|------|----|----|
| 业务角色 | 提单用户 | 处理人 |
| 用户名 | `u1_auto` | `u2_auto` |
| 显示名 | 自动化提单用户 | 自动化处理人 |
| 状态 | `active` | `active` |
| 系统权限 | `["user"]` | `["user"]` |
| 所属项目 | `Leo_test` | `Leo_test` |
| 项目角色 | 普通用户 `role_ef0c8cf7` | 普通用户 `role_ef0c8cf7` |

创建方式：

1. 使用后台管理员登录 `POST /api/auth/login`。
2. `POST /api/admin/users` 创建两个用户。
3. `POST /api/admin/users/{username}/roles` 将两个用户分别绑定到 `Leo_test` 的
   `role_ef0c8cf7`。
4. 用两个账号分别调用登录和 `/api/auth/me`，确认登录、项目角色和显示名正确。

创建结果（2026-09-09）：

- 两个账号均已创建，状态为 `active`。
- 显示名分别为“自动化提单用户”和“自动化处理人”。
- 均已绑定 `Leo_test` 的 `role_ef0c8cf7`，同时保留系统默认 `user` 角色。
- 两个账号均已完成登录验证。
- 密码只保存到 GitHub repository secrets，不写入本文档或代码仓库。

## 5. 已确认与待确认

### 5.1 已确认

- U1/U2 的用户名和显示名采用第 4 节参数。
- 固定问答第一版采用 `problem` 工单类型。
- GitHub Actions 采用 SSH 端口转发访问测试环境后端。
- PR 仅跑 Mock/无 secret 测试；真实后端链路仅允许 `push develop` 和 `workflow_dispatch`。
- GitHub Actions 专用 SSH key 已限制为只能访问 `127.0.0.1:9400`，不能执行远程命令。

### 5.2 待确认

1. 手动触发 `real-access-check.yml`，确认 GitHub 托管 runner 可访问真实后端。
2. 可控 AI 回复的实现/配置方案。
3. 自动清理接口与权限。
