# 新门户（topo-ng）容器部署

旧版拓扑站（容器 `topo-portal`、网页 **8766**、容器 SSH **9022**、路径 `/opt/topo-portal`）在线上，用户在用。**本目录的任何脚本都不得连接、覆盖、重启它。**

新栈独立命名与端口：

| | 旧版（禁止碰） | 新版 topo-ng |
|--|--|--|
| Compose 项目 | （旧 deploy） | `topo-ng` |
| 容器 | `topo-portal` | `topo-ng-api` / `topo-ng-web` / `topo-ng-db` |
| 网页 | 8766 | **8776** |
| 容器 SSH | 9022 | **9122**（用户 `topo`） |
| 数据 | 文件卷 `topo-app-data` | Postgres 卷 `topo_ng_pg_data` |
| 应用路径 | `/opt/topo-portal/app/` | 容器内 `/app/app`（代码）+ `/app/www`（SPA） |

对话里说「更新服务器」默认只更新**新门户代码**。改数据库或密钥必须你另外说清楚。

---

## 原则

1. **源码与数据不外泄**
   - 服务器上不要 `git clone` 本仓库。
   - 密钥在**服务器本机**生成（`secrets/install.env`，mode 600），不进镜像、不进 git、不从开发机 scp。
   - Postgres **不**映射到宿主机端口。网页只出 nginx；API 只在容器网里。
2. **代码更新不得覆盖数据**
   - 用户数据在卷 `topo_ng_pg_data`。`update.sh` / `push-update-container.sh` 只 rsync `/app/app` 与 `/app/www`。
   - 没有可用的 `pg_restore` / 数据推送通道。`push-data-container.sh` 固定失败。
   - 禁止 `docker compose down -v`。
3. **操作前必须确认目标**
   - 首次启动与推代码：环境变量 `TOPO_NG_DEPLOY=1`。
   - 脚本见到 8766、9022、`topo-portal`、`/opt/topo-portal` 立刻退出。

---

## 架构

```
浏览器 ──► 宿主机 :8776 ──► 容器 topo-ng-web (nginx)
                              ├─ 静态 SPA  (/usr/share/nginx/html ← 卷 topo_ng_www)
                              └─ /api/*  ──► topo-ng-api:8767

Agent/你 ──► 宿主机 :9122 ──► 容器 topo-ng-api 内 sshd（用户 topo）
                                └─ uvicorn 读 /app/app，写库走内部主机名 db

topo-ng-db  仅内部网络 5432    卷 topo_ng_pg_data
```

容器内 SSH 与网页是两条线：更新代码时 `pkill uvicorn`，sshd 继续存活。

---

## 环境约定

| 变量 | 默认 | 说明 |
|------|------|------|
| `TOPO_NG_WEB_PORT` | 8776 | 网页（禁止 8766） |
| `TOPO_NG_SSH_PORT` | 9122 | 容器 SSH（禁止 9022） |
| `TOPO_NG_BIND` | `0.0.0.0` | 宿主机绑定地址 |
| `TOPO_SSH_PUBLIC_KEY` | （必填其一） | 写入容器 `topo` 用户 `authorized_keys` |
| `TOPO_SSH_PASSWORD` | （必填其一） | 无公钥时给 `topo` 设密码 |
| `TOPO_NG_DEPLOY` | 无 | 必须为 `1` 才能 `run-docker` / 推代码 |
| 宿主机解压目录 | 自定，例如 `~/topo-ng-deploy` | **不要**用 `/opt/topo-portal` |

在服务器 `secrets/install.env` 里可再设 `TOPO_PUBLIC_URL=http://<host>:8776`（授权包里的站点地址）。

---

## 一、本机打部署包（开发机）

不要把 `.env`、`secrets/`、数据库、授权 zip 打进去。

```bash
cd doc-service
./package-release.sh
./deploy/bundle-for-server.sh
# 得到 dist/topo-ng-deploy-latest.tar.gz
```

离线机房（服务器访问不了 Docker Hub）：在**开发机**拉镜像并导出。

```bash
cd doc-service/deploy
TOPO_NG_DEPLOY=1 ./export-docker-image.sh
# 得到 ../dist/topo-ng-image.tar.gz（topo-ng-api / web / db）
```

把 **两个** 文件拷到服务器宿主机（scp 走 22，不要 9022）：

- `dist/topo-ng-deploy-latest.tar.gz`
- `dist/topo-ng-image.tar.gz`

---

## 二、服务器首次建容器（宿主机）

前提：Docker + Compose；**不要**停旧版 `topo-portal`。服务器不能拉镜像时必须先 `import`，再 `TOPO_SKIP_BUILD=1`。

```bash
mkdir -p ~/topo-ng-deploy
tar xzf ~/topo-ng-deploy-latest.tar.gz -C ~/topo-ng-deploy
cd ~/topo-ng-deploy

./import-docker-image.sh ~/topo-ng-image.tar.gz

export TOPO_NG_DEPLOY=1
export TOPO_SKIP_BUILD=1
# 公钥来自【开发机】：cat ~/.ssh/id_rsa.pub
export TOPO_SSH_PUBLIC_KEY='ssh-rsa AAAA…开发机公钥整行'

# 首次会生成 secrets/install.env（管理员密码只显示一次，请抄走）
./run-docker.sh
```

`TOPO_SKIP_BUILD=1` 时不会访问 Docker Hub，只用已经 load 的 `topo-ng-*:local` 镜像。数据卷空库时 API 启动会建表并写入 `admin`。

验证（这些名字/端口必须对上，对不上就停）：

```bash
docker ps --format '{{.Names}} {{.Ports}}' | grep topo-ng
# 应看到 topo-ng-api / topo-ng-web / topo-ng-db
# 不应出现正在被你操作的 topo-portal

curl -sS "http://127.0.0.1:8776/api/v1/health"
ssh -p 9122 topo@<host>
```

浏览器打开 `http://<host>:8776/`。旧站 `http://<host>:8766/` 应仍是原来的服务。

---

## 三、对话更新代码（开发机 → 容器 SSH）

你确认目标是新门户之后，在开发机：

```bash
cd doc-service
TOPO_NG_DEPLOY=1 ./deploy/push-update-container.sh topo@<host>
# 默认 -p 9122
```

流程：`package-release.sh` → scp 进 **9122** → 容器内 `update.sh`（只覆盖 `/app/app` 与 `/app/www`）→ `pkill uvicorn` → entrypoint 拉起新进程。

Postgres 卷、`secrets/install.env`、旧版 8766 都不在这条路径上。

覆盖默认端口（仍然禁止 9022）：

```bash
TOPO_NG_DEPLOY=1 ./deploy/push-update-container.sh topo@<host> --ssh-port 9122
```

---

## 四、脚本清单

| 脚本 | 在哪跑 | 作用 |
|------|--------|------|
| `package-release.sh` | 开发机 | 打 `app/` + `www/` 代码包 |
| `deploy/bundle-for-server.sh` | 开发机 | 打给宿主机的 `deploy/` 包 |
| `deploy/run-docker.sh` | 服务器宿主机 | 建 **topo-ng** 三个容器 |
| `deploy/push-update-container.sh` | 开发机 | SSH 进容器更新代码 |
| `deploy/export-docker-image.sh` / `import-docker-image.sh` | 开发机 / 宿主机 | 离线镜像 |
| `deploy/gen-secrets.py` | 服务器 | `--db-host db --write secrets/install.env` |
| `deploy/selfcheck.sh` | 开发机 | 改 deploy 之后必跑 |
| `deploy/push-data-container.sh` | — | 故意失败，防止误推数据 |

容器内：`/app/bin/update.sh`、`/usr/sbin/sshd`、uvicorn 循环。

---

## 五、改数据

只有你明确说「改服务器上的数据 / 灌库 / 换密钥」时才动手。默认通道不存在。即便以后加，也必须与代码更新分开，并再次确认不是旧版 `topo-portal`。
