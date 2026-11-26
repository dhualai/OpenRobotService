# 工单状态只读 API（供 CI 调用）

CI 需要知道「这张工单是不是已经解决/关闭」，以前的做法是从 GitHub 托管 runner 开 SSH 隧道直连生产 MySQL。
现在改成：在云服务器上跑一个**只读小服务**，CI 用 HTTPS + token 查一个工单状态。

```
GitHub Actions ──HTTPS──▶ ticket-status API ──SELECT──▶ MySQL(只读账号)
```

好处：

- CI 不再持有 SSH 私钥和数据库口令，只持有一个 API token
- 不需要 SSH 隧道，也不依赖自托管 runner
- 应用层可审计（谁在什么时候查了哪张工单）、可限流
- 服务只暴露一个字段接口，不能读别的表

## 接口

### `GET /health`

无鉴权，返回 `{"status":"ok","version":"1.0.0"}`。

### `GET /tickets/{ticket_id}`

需要 `Authorization: Bearer <TICKET_API_TOKEN>`。

```json
{
  "ticket_id": 1053,
  "status": "resolved",
  "status_known": true,
  "project_id": "Leo_test",
  "task_type": "bug",
  "eligible": true
}
```

| 字段 | 含义 |
| --- | --- |
| `status` | 数据库里的原始状态（小写） |
| `eligible` | 是否 `resolved` 或 `closed`——**CI 只认这个字段** |
| `status_known` | 状态值是否在已知集合内（防止新增状态被误判） |

错误：`400` 工单号格式错、`401` token 错、`404` 工单不存在、`503` 数据库不可用。

**503 是有意的**：数据库挂了不能返回一个假的「未关闭」，CI 必须当成「无法判断」处理。

## 部署（在云服务器上）

### 1. 建运行账号和目录

```bash
sudo useradd --system --home /opt/ticket-status --shell /usr/sbin/nologin ticket-api
sudo mkdir -p /opt/ticket-status
sudo chown ticket-api:ticket-api /opt/ticket-status
```

### 2. 放代码

把本目录的 `app.py`、`requirements.txt` 拷到 `/opt/ticket-status/`。

### 3. 装依赖

```bash
cd /opt/ticket-status
sudo -u ticket-api python3 -m venv .venv
sudo -u ticket-api .venv/bin/pip install -r requirements.txt
```

如果服务器连不上 PyPI，用内部镜像：

```bash
sudo -u ticket-api .venv/bin/pip install -r requirements.txt -i <内部源>
```

### 4. 配置

`/opt/ticket-status/ticket-status.env`（权限 `600`，属主 `ticket-api`）：

```bash
TICKET_API_TOKEN=<随机生成的强口令>
TICKET_DB_HOST=127.0.0.1
TICKET_DB_PORT=3306
TICKET_DB_NAME=helpdesk_724
TICKET_DB_USER=openrobot_ro
TICKET_DB_PASSWORD=<只读账号口令>
```

```bash
sudo chown ticket-api:ticket-api /opt/ticket-status/ticket-status.env
sudo chmod 600 /opt/ticket-status/ticket-status.env
```

### 5. 起服务

```bash
sudo cp ticket-status.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ticket-status
sudo systemctl status ticket-status
```

### 6. 本机自测

```bash
curl -s localhost:8099/health
curl -s -H "Authorization: Bearer $TICKET_API_TOKEN" localhost:8099/tickets/1053
```

### 7. 对外暴露（关键）

服务只监听 `127.0.0.1`，必须经反向代理才能被 CI 访问。推荐 nginx：

```nginx
location /ticket-status/ {
    # 只允许读接口
    limit_except GET { deny all; }
    limit_req zone=api burst=5 nodelay;

    proxy_pass http://127.0.0.1:8099/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

要点：

- **必须 HTTPS**，token 不能走明文
- **只允许 GET**，禁用其他方法
- **限流**：CI 一次只查一两个工单，不需要高并发
- 建议在网关再加一层 IP 白名单（GitHub 的 IP 段见 `api.github.com/meta`），能挡掉大部分扫描

## GitHub 侧配置

仓库 Secrets：

| Secret | 值 |
| --- | --- |
| `TICKET_STATUS_API_URL` | `https://<域名>/ticket-status` |
| `TICKET_STATUS_API_TOKEN` | 上面那个 token |

以下 4 个可以删除：

```
PROD_TICKET_SSH_PRIVATE_KEY
PROD_TICKET_SSH_USER
PROD_TICKET_DB_USER
PROD_TICKET_DB_PASSWORD
```

## 运维注意

- token 泄露的后果是「能查工单状态」，不是「能进服务器」——这是相对 SSH 方案最大的改进
- 轮换 token：改 `ticket-status.env` → `systemctl restart ticket-status` → 同步改 GitHub Secret
- 日志：`journalctl -u ticket-status -f`，关注 401（token 错）和 503（数据库不可用）
- 服务本身不写库，没有任何写权限，数据库账号也只 `SELECT`
