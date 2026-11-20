# 拓扑门户（绿场）

本机实验项目。旧门户 `docs/algo-capability-topology` 只读；**不要**对它的服务器端口 8766 / SSH 9022 / 容器 `topo-portal` 做任何 deploy/scp。

新门户容器部署见 `deploy/DEPLOY.md`（独立端口 8776 / SSH 9122，项目名 `topo-ng`）。

## 本机启动

```bash
cd doc-service
python3 packaging/gen-secrets.py --write .env
python3 -m venv server/.venv
server/.venv/bin/pip install -e "server/[dev]"
docker compose up -d
cd server && .venv/bin/pytest
```

日常开站（本机终端，Ctrl+C 停）：

```bash
cd doc-service && ./scripts/dev-up.sh
```

页面 `http://127.0.0.1:5173/`，API `http://127.0.0.1:8767`。Postgres 只绑 `127.0.0.1:5432`。发行形态见 `packaging/compose.yml`（不发布 5432）。

前端（需本机 Node）：`cd web && npm i && npm run dev`，代理 `/api` → `127.0.0.1:8767`。

API：`cd server && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8767 --reload`

本机未装系统 Node 时，发行包在 `.tools/node-*`（gitignore）。前端：`./scripts/dev-web.sh` → `http://127.0.0.1:5173/`。


## 测试

目录：`tests/CATALOG.md`。跑：`cd server && pytest`。
无 Postgres 时 API 用例 skip，crypto/policy/packaging 仍跑。

## 保密

正文 AES-GCM 信封加密。`TOPO_CONTENT_KEY` 不进镜像。拷走数据卷解不开正文。
