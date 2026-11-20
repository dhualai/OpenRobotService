# 智库（doc-service）挂到摇人吧 test 环境 · 部署说明

把严青投递的智库服务，作为**独立微服务**挂到摇人吧 test 环境（`/t/app/kb/`），
与摇人吧**同源**（`https://usp.ep-zl.com`），零 CORS，账号统一走摇人吧登录态。

> 本目录是「挂载适配」资产。**没有**改动摇人吧的 `deploy/deploy.py` 与 CI 流水线——
> 那是有意的：先用手工步骤把链路跑通、验证稳定后，再评估是否并入正式部署流程。

## 目标形态

```
浏览器 https://usp.ep-zl.com/t/app/kb/          → mmp nginx → 静态产物（test/kb/）
浏览器 https://usp.ep-zl.com/t/app/kb/api/v1/…  → mmp nginx → 127.0.0.1:9420
                                                                    │
                                              supervisor: test-open-robot-kb
                                                                    │
                                          独立 PG 容器 kb-portal-test-db:5433
                                                                    │
                                    ORS_BASE_URL=127.0.0.1:9400（摇人吧 test 后端）
                                    用于校验 Bearer token / 拉用户权限
```

四个组件彼此隔离：PG 独立卷、API 独立进程、前端独立目录、nginx 独立 location。
与严青的 topo-ng 栈（8776/9122/topo-ng-db）**完全不共用**任何资源。

## 前置条件

| 项 | 说明 |
|----|------|
| 服务器 | 125.122.97.107（摇人吧所在机） |
| 账号 | `usp-a`（在 supervisor 组，`supervisorctl` 免 sudo） |
| **root 配合** | ① 写 `/etc/supervisor/conf.d/` ② 改 `/data/apps/mmp/nginx/conf/nginx.conf` 并 reload——这两步 `usp-a` 做不了，需运维 |
| 摇人吧 test 后端 | 已监听 `127.0.0.1:9400`（账号统一校验的目标） |
| 权限码 | 摇人吧侧已给目标用户加 `frontend:kb:write`（否则进智库是只读） |

## 步骤

### 1. 起独立 Postgres

```bash
cd doc-service/deploy/ors
export KB_PG_PASSWORD='<随机长口令，离线保管>'
docker compose -f docker-compose.kb-test.yml up -d
docker compose -f docker-compose.kb-test.yml ps     # 等 healthy
```

只绑 `127.0.0.1:5433`，不对公网暴露。

### 2. 放代码 + 建表

```bash
sudo mkdir -p /data/apps/TestOpenRobotService/kb-portal
# 把仓库 doc-service/{server,web} 上传到 /data/apps/TestOpenRobotService/kb-portal/
cd /data/apps/TestOpenRobotService/kb-portal/server
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/alembic upgrade head          # 18 个迁移，建全部表
```

### 3. 配环境变量

至少四项（缺一项服务拒绝启动，fail-closed）：

| 变量 | 值 |
|------|----|
| `DATABASE_URL` | `postgresql+psycopg://topo:<KB_PG_PASSWORD>@127.0.0.1:5433/topo` |
| `TOPO_CONTENT_KEY` | 32 字节以上随机串（正文 AES-GCM 加密密钥，**丢了现有内容永久解不开**） |
| `SESSION_SECRET` | 16 字节以上随机串 |
| `ORS_BASE_URL` | `http://127.0.0.1:9400` |

### 4. 起 API 进程（需 root）

```bash
sudo cp deploy/ors/supervisor-test-open-robot-kb.conf /etc/supervisor/conf.d/
sudo sed -i 's/CHANGE_ME_[a-z_]*/<真实值>/g' /etc/supervisor/conf.d/test-open-robot-kb.conf
supervisorctl reread && supervisorctl update
supervisorctl status test-open-robot-kb
curl -s 127.0.0.1:9420/api/v1/health      # 期望 {"ok":true,...}
```

### 5. 发前端（带 base 构建）

```bash
cd doc-service/web
npm ci
VITE_BASE=/t/app/kb/ npm run build        # 产物在 dist/
# 上传到服务器（保持只替换 kb 子目录，不动 test 主前端）：
sudo mkdir -p /data/apps/TestOpenRobotService/nginx/html/test/kb
# 清空 kb/ 后把 dist/ 内容解压进去
```

### 6. 加 nginx location（需 root）

把 `deploy/ors/nginx-kb-test.location.conf` 里两段**原样追加**到
`/data/apps/mmp/nginx/conf/nginx.conf` 中 `server_name usp.ep-zl.com` 的 server 块内
（与 `/t/app/`、`/t/api/` 并列），然后：

```bash
sudo nginx -t -c /data/apps/mmp/nginx/conf/nginx.conf
sudo nginx -s reload -c /data/apps/mmp/nginx/conf/nginx.conf   # 或重启 mmp nginx
```

## 验证清单

| # | 检查 | 期望 |
|---|------|------|
| 1 | `curl -I https://usp.ep-zl.com/t/app/kb/` | 200，HTML 里资源路径是 `/t/app/kb/assets/...` |
| 2 | 未登录访问 `/t/app/kb/` | 跳转到 `/t/app/login?from=...` |
| 3 | 登录后访问 `/t/app/kb/` | 正常进拓扑页，`/api/v1/me` 200 |
| 4 | 有 `frontend:kb:write` 的账号 | 能编辑模块/发布版本 |
| 5 | 无该权限的账号 | 只读（`role=guest`） |
| 6 | 静置 30 分钟以上再操作 | 不被踢（refresh 自动续期生效） |
| 7 | `supervisorctl status test-open-robot-kb` | RUNNING |
| 8 | 摇人吧 test 前端 `/t/app/` | 不受影响（回归） |

## 回滚

| 层级 | 做法 |
|------|------|
| nginx | 删掉追加的两段 location，`nginx -t && reload`（立即生效，不影响摇人吧） |
| 进程 | `supervisorctl stop test-open-robot-kb` |
| 前端 | 删掉 `nginx/html/test/kb/` 目录 |
| 数据 | PG 卷 `kb_portal_test_pg_data` 保留（**不要** `down -v`） |

## 已知待办

1. **未并入 `deploy/deploy.py`**：目前是手工步骤。跑通并稳定后，建议把
   「PG 容器 + server 代码 + 前端 dist」作为一个新组件加进 deploy.py，
   与 test 环境一起备份/回滚。
2. **生产环境**：把路径前缀换成 `/p/app/kb/`、supervisor 程序名换成
   `openrobot-kb`（与 prod 的 `openrobot`/`openrobotAI` 对齐）、
   PG 用独立的一套（不要复用 test 库）。
3. **`importer/` 不在投递分支里**：灌历史数据需要另外补工具。
4. **带 PG 的完整用例**尚未在服务器实跑（本机无 Postgres）；上线后应补跑
   `doc-service/server` 的 pytest 全量。
