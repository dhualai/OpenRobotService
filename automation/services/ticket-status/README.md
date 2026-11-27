# 工单状态查询：两个脚本

CI 需要知道「这张工单生产上是否已解决/关闭」。以前是开 SSH 隧道直连 MySQL，现在拆成两个脚本：

| 文件 | 跑在哪 | 作用 |
| --- | --- | --- |
| `server.py` | 云服务器（MySQL 所在机器） | 单文件只读 HTTP 接口，`GET /tickets/1053` |
| `query.sh` | CI / 本地 | 用 curl 调上面的接口 |

依赖只有 `python3` 和 `mysql` 客户端，不需要装包、不需要虚拟环境。

## 一、服务器上跑起来

```bash
export TICKET_API_TOKEN='<随机串>'
export TICKET_DB_PASSWORD='<只读账号口令>'
export TICKET_DB_USER='openrobot_ro'      # 默认值，按实际改
python3 server.py
```

默认只监听 `127.0.0.1:8099`——服务本身不带 TLS，绑 `0.0.0.0` 等于把带凭据的接口明文暴露到公网。
要对外提供访问，请在前面加 HTTPS 反向代理；确有需要时才设置 `TICKET_API_ALLOW_PUBLIC=1` 并把 BIND 改成 `0.0.0.0`。
端口可用 `TICKET_API_PORT` 改。

自测：

```bash
curl -s localhost:8099/health
curl -s -H "Authorization: Bearer $TICKET_API_TOKEN" localhost:8099/tickets/1053
```

### 让它常驻

```bash
nohup env TICKET_API_TOKEN=... TICKET_DB_PASSWORD=... python3 server.py >> /var/log/ticket-status.log 2>&1 &
```

建议再写个 systemd 单元，好处是挂了自动拉起、开机自启。

## 二、CI 侧

仓库加两个 Secret：

| Secret | 值 |
| --- | --- |
| `TICKET_STATUS_API_URL` | `https://<域名或IP>:8099` |
| `TICKET_STATUS_API_TOKEN` | 上面那个 token |

原来这 4 个可以删掉：

```
PROD_TICKET_SSH_PRIVATE_KEY
PROD_TICKET_SSH_USER
PROD_TICKET_DB_USER
PROD_TICKET_DB_PASSWORD
```

## 接口

`GET /tickets/{id}`，需要 `Authorization: Bearer <token>`：

```json
{"ticket_id": 1053, "status": "resolved", "eligible": true, "project_id": "Leo_test", "task_type": "bug"}
```

- `eligible` 是 `resolved` 或 `closed` 时为真，CI 只认这个字段
- 数据库查不了返回 **503**，不会编一个「未关闭」出来
- 只支持 GET，其他方法返回 405

## 安全注意

- **不要直接暴露到公网用 HTTP**。至少要放在 nginx 后面上 HTTPS，或者只允许 CI 所在网络访问
- token 泄露的后果是「能查工单状态」，不能进服务器、不能写库
- 数据库账号只要 `SELECT`，只给 `tasks` 一张表
- 轮换 token：改环境变量 → 重启进程 → 同步改 GitHub Secret

## 为什么不直接用 FastAPI

这服务只有一个接口、一天查几次。标准库的 `http.server` 够了，少一层依赖就少一处「服务器装不上包」的故障点。
