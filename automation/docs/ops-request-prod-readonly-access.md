# 【申请】自动化测试 CI 生产数据库只读接入

- **申请人**：＿＿＿＿
- **日期**：＿＿＿＿
- **用途**：OpenRobotService 项目自动化测试门禁（GitHub Actions）
- **紧急度**：中（不影响线上业务，但阻塞工单闭环验证）

## 一、申请事由

`test` 分支挂有工单门禁（`.github/workflows/ticket-gate.yml`），逻辑是：

> 代码合并前，先查这张工单在生产库里**是否已解决/关闭**；
> 已解决 → 执行该工单对应的功能用例；未解决 → 跳过（避免测试未完成的功能）。

当前缺少"查工单状态"的只读通道，门禁只能跑回归用例，**功能用例一直是 0 条**。

## 二、需要什么

### ① SSH 账号（运维提供）

| 项 | 需求 |
|---|---|
| 主机 | `125.122.97.107` |
| 端口 | `8802` |
| 账号 | `usp-a`，或**新建专用只读账号**（推荐） |
| 认证方式 | **私钥**（非密码） |
| 私钥来源 | 单独生成一把，**仅用于 CI**，不要复用个人密钥 |

### ② 数据库只读账号（DBA 提供）

| 项 | 需求 |
|---|---|
| 库 | `helpdesk_724` |
| 表 | 仅 `tasks` |
| 权限 | **仅 `SELECT`**，不需要 INSERT / UPDATE / DELETE / DDL |
| 账号 | 新建，如 `openrobot_ro` |
| 密码 | 随机强密码 |

建号语句（供 DBA 参考）：

```sql
CREATE USER 'openrobot_ro'@'127.0.0.1' IDENTIFIED BY '<随机强密码>';
GRANT SELECT ON helpdesk_724.tasks TO 'openrobot_ro'@'127.0.0.1';
FLUSH PRIVILEGES;
```

## 三、实际会执行的 SQL（仅这一条）

```sql
SELECT id, title, status, task_type, project_id, created_at
FROM tasks
WHERE project_id = 'Leo_test'
  AND status IN ('new')
  AND task_type IN ('feature', 'bug')
ORDER BY created_at DESC
LIMIT 20;
```

**全程只读**：不写入、不修改、不建表、不删数据。

连接方式：CI 通过 SSH 建立**临时端口转发**（用完立即断开），不直连数据库端口。
实现见 `automation/src/ticket_pipeline/scanner.py::SSHMySQLTicketSource`。

> 数据流动范围：查询结果中的 `title` / `description` 等字段会进入 GitHub Actions
> runner，用于生成测试用例。**该数据外发范围已由业务方确认可以接受。**

## 四、安全限制要求

请在 `authorized_keys` 中为这把 CI 专用密钥加限制：

```
no-pty,no-agent-forwarding,no-X11-forwarding,no-user-rc,permitopen="127.0.0.1:3306",command="/bin/false" ssh-ed25519 AAAA... ci-automation
```

各参数作用：

| 参数 | 作用 |
|---|---|
| `no-pty` / `no-user-rc` / `command="/bin/false"` | 无法登录 shell、无法执行命令 |
| `no-agent-forwarding` / `no-X11-forwarding` | 禁止转发认证代理和图形通道 |
| **`permitopen="127.0.0.1:3306"`** | **只允许端口转发到 MySQL 3306，转发到内网其他服务一律拒绝** |

> ⚠️ `permitopen` 是关键：缺少它时，这把密钥虽然登不上 shell，但仍能在跳板机上
> 建立通往任意可达内网服务（Redis / ES / 其他数据库）的隧道，等同于一个内网跳板。

## 四之二、仓库侧配套防护

| 措施 | 作用 |
|---|---|
| `.github/workflows/` 配置 `CODEOWNERS` + branch protection | 限制只有指定人员能修改 workflow，防止有人通过改 workflow 读取 Secret |
| 收紧可合并到 `test` 分支的人员范围 | 缩小凭据被滥用的入口 |

> 说明：GitHub Secrets 本身加密存储、日志自动脱敏，但**拥有 workflow 修改权限的人**
> 理论上可以构造步骤把 Secret 输出出来。真正的安全边界是仓库写权限。

## 五、为什么不使用 root

当前代码中数据库账号默认是 `root`（`scanner.py:100`），我们**不希望**在生产使用：

| 风险 | 说明 |
|---|---|
| 权限过大 | root 可 DROP / UPDATE 全库，一旦 CI 配置或密钥泄露后果不可控 |
| 无法审计 | 多条 CI 流水线共用 root，异常操作无法定位来源 |
| 违反最小权限原则 | 业务只需要读取 `tasks` 单表 |

配套改动（我方提交）：让 workflow 支持传入只读账号。

```yaml
# .github/workflows/ticket-gate.yml
PROD_TICKET_DB_USER: ${{ secrets.PROD_TICKET_DB_USER }}
```

## 六、凭据存放方式

全部存放于 GitHub 仓库 **Secrets**（加密存储、日志自动脱敏、普通成员不可读）：

| Secret 名称 | 内容 |
|---|---|
| `PROD_TICKET_SSH_HOST` | `125.122.97.107` |
| `PROD_TICKET_SSH_PORT` | `8802` |
| `PROD_TICKET_SSH_USER` | 专用账号 |
| `PROD_TICKET_SSH_PRIVATE_KEY` | 私钥全文 |
| `PROD_TICKET_DB_USER` | `openrobot_ro` |
| `PROD_TICKET_DB_PASSWORD` | 只读账号密码 |

## 七、有效期与轮换

- 建议有效期 **90 天**，到期轮换（缩短泄露窗口）
- 人员变动或密钥疑似泄露时，请立即吊销并通知我方
- 每次使用均有 GitHub Actions 运行日志可追溯

## 八、如果暂不方便开通

门禁会自动降级为**只跑回归用例**（当前状态）：

```
⚠️ 生产工单只读凭据未配置完整，工单状态不可读
⚠️ 门禁按 fail-closed 处理：跳过功能用例，仅执行回归
```

不会报错、不会阻断合并；但**工单闭环（功能用例）无法验证**。

## 九、审批

| 角色 | 姓名 | 意见 | 日期 |
|---|---|---|---|
| 运维 | | | |
| DBA | | | |
| 上级 | | | |
