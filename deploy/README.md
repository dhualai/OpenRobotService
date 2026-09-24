# 部署流水线（GitHub Actions 一键发布）

在网页上点按钮即可把 **后端 / AI / 前端** 发布到 test 或生产环境，无需本地环境：
构建、备份、上传、重启、健康检查、失败自动回滚全部在流水线内完成。

| 入口 | 位置 | 作用 |
| --- | --- | --- |
| 发布 | `Actions → Deploy → Run workflow` | 门禁 → 备份 → 构建上传 → 重启 → 健康检查 → 失败自动回滚 |
| 回滚 | `Actions → Rollback → Run workflow` | 先列可用备份，再按 id 还原并重启，无需手敲 SSH |

- 流水线定义：[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)、[`.github/workflows/rollback.yml`](../.github/workflows/rollback.yml)
- 执行逻辑：[`deploy.py`](deploy.py)（本地 CLI 与 CI 共用同一套实现，避免两套行为漂移）
- 通知脚本：[`notify.py`](notify.py)（企业微信 / 飞书群机器人）

## 一、首次启用（一次性）

### 1. 分支要求

`workflow_dispatch` 的按钮只在**默认分支（main）**存在该文件时显示，因此：

- `deploy.yml` / `rollback.yml` 需合入 `main`；
- 真正被部署的代码取自「代码分支」输入（默认 test→`test`、prod→`dev`），
  所以 **`test` 与 `dev` 分支也必须包含新版 `deploy/deploy.py`**（带 `--yes` / `--rollback` / `--list-backups` 等参数），否则流水线会因“未知参数”失败。

### 2. Secrets / Variables

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `TEST_SSH_PRIVATE_KEY` | Secret | 是（复用已有） | usp-a 私钥，多条既有 workflow 已在用 |
| `NOTIFY_WEBHOOKS` | Secret | 否 | 多群推送，逗号分隔，每项写 `<url>\|<policy>\|<群名>`（后两段可省）：`always`（默认，每条都发）/ `failure`（仅失败或自动回滚）/ `success`（仅成功）/ `off`（永久禁发，仅留档 URL）；群名仅作日志标签。例：`群A的url\|always\|研发群,群B的url\|failure\|运维群,群C的url\|off\|勿扰群`。<br>注意：**没写进本变量的群本来就不会收到通知**（不在名单 = 不发） |
| `NOTIFY_WEBHOOK` | Secret | 否 | 单群 Webhook（旧变量，作为 `NOTIFY_WEBHOOKS` 的回退，等价 `always`） |
| `NOTIFY_PROVIDER` | Variable | 否 | `wecom`（默认）/ `feishu` |
| `DEPLOY_SSH_HOST` | Variable | 否 | 缺省回退 `UI_REGRESSION_SSH_HOST` |
| `DEPLOY_SSH_USER` | Variable | 否 | 缺省 `usp-a` |
| `DEPLOY_SSH_PORT` | Variable | 否 | 缺省 `8802` |
| `DEPLOY_REMOTE_TMP` | Variable | 否 | 缺省 `~/tmp`（服务器 `/tmp` 与 `/data/tmp` 有 sticky/root 权限限制） |
| `DEPLOY_TEST_HEALTH_URLS` | Variable | 否 | 见「健康检查默认地址」 |
| `DEPLOY_PROD_HEALTH_URLS` | Variable | 否 | 见「健康检查默认地址」 |
| `DEPLOY_BACKUP_KEEP` | Variable | 否 | 保留备份份数，缺省 `10` |

即 **零新增配置也能直接跑**：SSH 连接信息从既有的 `UI_REGRESSION_*` 回退，健康地址内置实测默认值。

### 3. 健康检查默认地址（均已实测 HTTP 200）

| 环境 | 检查项 |
| --- | --- |
| test | `http://127.0.0.1:9400/api/health`（后端）、`http://127.0.0.1:9411/health`（AI）、`https://usp.ep-zl.com/t/app/`（前端） |
| prod | `http://127.0.0.1:8400/api/health`（后端）、`http://127.0.0.1:8401/health`（AI）、`https://usp.ep-zl.com/p/app/`（前端） |

由**服务器本机** curl 检查（后端/AI 未对公网开放），任一项失败即判定部署失败并自动回滚。

## 二、发布（Deploy）

参数：

| 参数 | 说明 |
| --- | --- |
| `environment` | `test` / `prod`；选 prod 必须在 `confirm` 填 `DEPLOY-PROD` |
| `components` | `all` / `frontend` / `backend` / `ai` |
| `git_ref` | 代码分支；留空则 test→`test`、prod→`dev` |
| `skip_gate` | 紧急跳过测试门禁；日志 / 摘要 / 通知都会标注 |
| `confirm` | 生产确认词 `DEPLOY-PROD` |

执行流程：

1. **guard**：校验生产确认词，解析分支与健康检查地址，输出「部署计划」表格；
2. **gate**：`backend|all → pytest --ignore=tests/tasks`、`frontend|all → vitest run`；
   失败即硬拦（不构建不部署）。`ai` 组件无自动门禁（`ai/tests` 依赖外部服务），需人工验证；
3. **deploy**：`npm ci` → `deploy.py` 备份远端 → 构建打包上传 → `supervisorctl restart` →
   健康检查（默认重试 20 次 × 间隔 3 秒）；
4. 健康检查失败 → **自动回滚到本次部署前的备份**并重启，流水线标记失败；
5. Summary 与通知给出备份 id、是否发生回滚、Actions 链接；`deploy.log` 作为 artifact 保留 30 天。

同一环境并发互斥（`concurrency`），不会出现两人同时发布互相覆盖。

## 三、回滚（Rollback）

`workflow_dispatch` 的下拉选项是静态的，无法动态列出服务器上的备份，因此采用「先列后选」两步：

1. `action=list`（默认）→ 在 Summary 中列出所有可用备份（id / 创建时间 / 提交 / 组件 / 大小）；
2. 再运行一次，`action=rollback`，把 id 填入 `backup_id`（留空或 `latest` = 最近一份）；
   prod 需在 `confirm` 填 `ROLLBACK-PROD`。

回滚会还原对应组件并重启受影响的服务，随后同样执行健康检查。
备份与回滚组件严格对应：**只还原备份中存在的组件包**（例如前端-only 备份不会动后端）。

## 四、备份约定

远端目录：`$HOME/deploy_backups/{env}/{backup_id}/`

```
$HOME/deploy_backups/test/20260923-152741-381030/
├── frontend.tar.gz / backend.tar.gz / ai.tar.gz   # 仅本次要覆盖的组件
└── manifest.json                                  # id、环境、时间、来源(local|ci)、提交号、组件、各包大小与 sha256
```

- 备份 id 形如 `YYYYMMDD-HHMMSS-<6位随机>`，服务端做白名单校验（防路径穿越 / 命令注入）；
- 每次部署前自动备份，保留最近 N 份（`DEPLOY_BACKUP_KEEP`，默认 10），超出部分自动清理；
- 备份排除 `ai/kb`、`ai/embed_models`、`.venv`、日志、`uploads` 等非代码内容，体积可控（实测前端包约 9.5 MB）；
- 只有包含 `manifest.json` 的目录才视为有效备份；
- **备份或复核失败会中止部署**，不会在没有备份的情况下覆盖线上。

## 五、故障排查

| 现象 | 排查方向 |
| --- | --- |
| Deploy 页面没有 Run workflow 按钮 | 文件尚未合入默认分支 `main` |
| `unrecognized arguments: --yes / --rollback` | 目标分支 `test` / `dev` 上的 `deploy.py` 仍是旧版，需先合入 |
| 门禁失败（gate 红） | 看 gate 日志跑对应测试；紧急发布可勾选 `skip_gate`（会被显著标注） |
| 前端构建失败在 `tsc -b` | 类型检查未过，本地 `npm run build:test` 可复现 |
| SSH 连接失败 | 检查 `TEST_SSH_PRIVATE_KEY` 与 `DEPLOY_SSH_*`；preflight 步骤会打印远端 `supervisorctl status` |
| 健康检查失败并自动回滚 | Summary 标注「自动回滚=是」，需人工确认服务；必要时再 Rollback 到更早备份 |
| 没收到通知 | 未配置 `NOTIFY_WEBHOOKS` / `NOTIFY_WEBHOOK` 会跳过；Webhook 域名仅允许企业微信 / 飞书（防 SSRF）；某群策略与本次结果不匹配时也会跳过（如 `failure` 群在部署成功时不发），日志会逐条打印每个目标的发送 / 跳过原因 |
| 备份占用磁盘 | 调小 `DEPLOY_BACKUP_KEEP`，或到服务器清理 `~/deploy_backups/<env>/` |

## 六、本地等价操作

```bash
# 预览（不产生任何远端改动）
python deploy/deploy.py -e test -c all --dry-run

# 真实部署（需本地具备 tar / scp / ssh / npm）
python deploy/deploy.py -e test -c all \
  --ssh-host 125.122.97.107 --ssh-user usp-a --ssh-port 8802 \
  --ssh-identity ~/.ssh/id_ed25519 \
  --health-urls "http://127.0.0.1:9400/api/health,https://usp.ep-zl.com/t/app/"

# 列出备份 / 回滚
python deploy/deploy.py -e test --list-backups --backups-format md \
  --ssh-host 125.122.97.107 --ssh-user usp-a --ssh-port 8802 --ssh-identity ~/.ssh/id_ed25519
python deploy/deploy.py -e test --rollback latest \
  --ssh-host 125.122.97.107 --ssh-user usp-a --ssh-port 8802 --ssh-identity ~/.ssh/id_ed25519 \
  --health-urls "http://127.0.0.1:9400/api/health,https://usp.ep-zl.com/t/app/"
```

## 七、安全边界（本仓库为 public，务必知悉）

**开源代码 ≠ 开放部署权限。** 外部人员可查看代码、workflow 定义与 Actions 日志，但**无法触发部署、无法读取 Secrets**：

| 能力 | 非项目人员 | 有 Write 权限的成员 |
| --- | --- | --- |
| 查看代码 / workflow / Actions 日志 | 可以（public 仓库） | 可以 |
| 点击 `Run workflow` 触发部署 | **不可以**（按钮不可用，API 403） | 可以 |
| 读取 `TEST_SSH_PRIVATE_KEY` 等 Secrets | **不可以**（日志打码；fork PR 按设计也拿不到） | 不能直接读取，但**改 workflow 可间接取用** |
| 修改 `deploy.py` / workflow 并生效 | 不可以（需 PR + review + ruleset 保护） | 需 PR 合并后才生效 |

关键机制与纪律：

1. **触发权限**：`workflow_dispatch` 仅有仓库 **Write 权限**者能触发；本仓库 8 个 fork 均为只读，无法触发。
2. **Secrets 隔离**：Secrets 不会出现在日志（GitHub 自动打码），fork 提交的 PR 事件按设计拿不到 Secrets；
   本仓库 workflow **未使用** `pull_request_target` / `workflow_run`，不存在「以主仓库密钥执行外部代码」的经典漏洞模式。
3. **`git_ref` 白名单（防 secrets 被交给外部代码）**：guard 会拒绝含 `:` 的 ref（`owner:branch` 指向 fork）、
   `refs/pull/*` 等非分支 ref、以 `-` 开头或含 shell 元字符的值，并要求分支**确实存在于本仓库**。
   因此**不要**用他人 fork 的分支名去部署。
4. **workflow 文件即执行配置**：任何改动 `.github/workflows/*` 或 `deploy/deploy.py` 的 PR，
   等同「允许其在 CI 中以服务器密钥执行代码」，**review 必须严格**（尤其新增 `run` 步骤、把 Secret 转进 env/日志、上传到外部地址）。
   脚本内所有用户输入均经 `env` 传递，**不得**直接 `${{ inputs.* }}` 插值进 `run`（脚本注入）。
5. **日志公开**：public 仓库的日志与 Summary 对所有人可见 —— 非敏感连接信息（服务器地址/账号/端口/路径）会明文出现，
   这与团队既有的 `UI_REGRESSION_*` 做法一致；真正的机密是 SSH **私钥**（存于 Secrets），且服务器已禁用密码登录、仅公钥可用。
   **禁止**在 workflow 中 `echo` 任何 Secret 或把 Secret 写入日志与 artifact。
6. **凭据最小化**：流水线**不持有 sudo 密码** —— `usp-a` 属 `supervisor` 组，`supervisorctl restart` 免 sudo，故统一 `--no-sudo`。
7. **生产确认词不是密码**：`DEPLOY-PROD` / `ROLLBACK-PROD` 在 public 仓库中可见，仅用于**防误点**，不构成安全边界；
   真正的边界是触发权限、ref 白名单与 PR review。

> 已知残留风险：复用 `TEST_SSH_PRIVATE_KEY`（`usp-a` 身份 —— 该账号在服务器上有生产写目录与 supervisor 权限）。
> 这是既有 workflow 早已存在的现状，本次未扩大攻击面；如需进一步收敛，可新建仅有 test 权限、无 sudo 的专用部署账号并换用其密钥。

常用可选参数：`--no-backup`（跳过备份，不推荐）、`--backup-keep N`、`--remote-tmp DIR`、
`--yes`（跳过生产交互确认，CI 用）、`--no-sudo`（服务器已给 supervisor 组 socket 权限）。
