# 本地搭建与部署

## 环境要求

- Python 3.11+（实测 3.14 可用；注意 `asyncmy` 在 3.14 下无预编译 wheel 且源码编译失败，后端已加 `aiomysql` 回退，见「2. 后端」）
- MySQL 8.x（**避开 8.0.13**：该版 `DEFAULT (now())` 表达式默认值有缺陷，建表成功但之后 `CREATE INDEX`/`ALTER` 会误报 `Invalid default value for '...'`(1067)；用 **8.0.14+**，推荐 8.0.latest 或 8.4）
- Node.js 18+（前端）

## 1. 数据库准备

确保 MySQL 已启动，并创建数据库（utf8mb4）：

```sql
CREATE DATABASE IF NOT EXISTS openrobotservice
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> Windows 用户可直接运行仓库内 `scripts/setup_mysql.ps1`（管理员 PowerShell）一键完成安装与建库。

> **本地 Docker 方案（推荐，避免污染既有 MySQL / 踩 8.0.13 的坑）**：若本机已有旧版或共享 MySQL（尤其是 8.0.13），建议为本项目单独起一个 8.0.14+ 容器：
> ```bash
> docker run -d --name openrobot-mysql \
>   -e MYSQL_ROOT_PASSWORD=123456 -e MYSQL_DATABASE=helpdesk_7_16 \
>   -p 3307:3306 mysql:8.0 \
>   --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
> ```
> 再把 `backend/.env` 的 `DATABASE_URL` 端口改为 **3307**：`mysql+pymysql://root:123456@127.0.0.1:3307/helpdesk_7_16?charset=utf8mb4`（库名按需替换）。

## 2. 后端

```bash
cd backend

# 创建虚拟环境（仓库内现有目录名为 venv）
python -m venv venv
# 激活（Windows Git Bash）
source venv/Scripts/activate
# 激活（Windows PowerShell）
# venv\Scripts\Activate.ps1
# 激活（Linux/Mac）
# source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env：DATABASE_URL（注意端口——Docker 专用容器走 3307）、JWT 密钥、微信参数

# 建表：app/core/database.py 在导入时即 Base.metadata.create_all()（运行时兜底）；
# 也可走 Alembic 迁移 `alembic upgrade head`（目标态）。二者并存。

# （可选）初始化种子数据：管理员、示例项目
python -m app.seed

# 启动开发服务器（main.py 内部以 uvicorn 运行 "app:app"，端口 8400，reload）
python main.py
```

启动后访问交互式 API 文档：http://127.0.0.1:8400/docs（默认管理员 `admin / 123456`）

## 3. 环境变量说明（.env）

| 变量 | 说明 | 示例 |
|------|------|------|
| `DATABASE_URL` | MySQL 连接串 | `mysql+pymysql://root:123456@127.0.0.1:3307/helpdesk_7_16?charset=utf8mb4`（Docker 专用容器为 **3307**；本地原生 MySQL 仍用 3306） |
| `JWT_SECRET` | JWT 签名密钥（务必改成随机长串） | `change-me-to-a-random-secret` |
| `JWT_EXPIRE_MINUTES` | Token 有效期（分钟） | `10080` |
| `WECHAT_APP_ID` | 服务号 AppID | `wx1234567890abcdef` |
| `WECHAT_APP_SECRET` | 服务号 AppSecret | `xxxxxxxx` |
| `WECHAT_TOKEN` | 服务器配置 Token | `your_token` |
| `WECHAT_AES_KEY` | 消息加解密 Key（可选） | `xxxx` |
| `WECHAT_OAUTH_REDIRECT` | OAuth 回调地址 | `https://your.domain/api/wechat/oauth/callback` |
| `FRONTEND_BASE_URL` | 前端 H5 地址 | `https://your.domain` |
| `TPL_ASSIGN_ID` | 派单通知模板 ID | `xxx` |
| `TPL_COMMENT_ID` | 讨论通知模板 ID | `xxx` |
| `TPL_ESCALATE_ID` | 上报通知模板 ID | `xxx` |

> 没有微信参数也能跑：微信相关功能会降级（通知打印到日志），核心工单流程不受影响，可用 `/api/auth/dev-login` 登录联调。

## 4. 前端

```bash
cd frontend
npm install
npm run dev    # 开发，默认 http://127.0.0.1:5173
npm run build  # 生产构建，产物在 dist/
```

后端拆为**业务后端**（`backend/main.py`@8400）和**独立 AI 服务**（`ai/run.py`@8401），本地 dev 需**两者都启动**：

```bash
# 终端1：业务后端（/api/auth|admin|tasks|call|wechat）
cd backend && python main.py          # → http://127.0.0.1:8400

# 终端2：AI 服务（/api/ai/*）
cd .. && python ai/run.py             # → http://127.0.0.1:8401
```

`npm run dev` 时 `vite.config.ts` 代理拆分：`/api/ai/*`→8401、其余 `/api/*`→8400（目标可用 `VITE_DEV_BACKEND_TARGET` / `VITE_DEV_AI_TARGET` 覆盖）。前端登录守卫开关见 `frontend/.env.local` 的 `VITE_DISABLE_AUTH_GUARD`。

**生产构建**：经 nginx 按环境前缀分发（见 `deploy/nginx/conf/conf.d/app_gateway.conf`），需按环境构建并把产物分别放入 nginx `html/test/`、`html/prod/`：

```bash
npm run build:test   # base='/t/app/'，API 前缀推导为 /t/api（测试）
npm run build:prod   # base='/p/app/'，API 前缀推导为 /p/api（生产）
```

API 前缀由 `src/config/api.ts` 据 vite `base` 自动推导，nginx 用最长前缀把 `.../api/ai/*` 转给 AI 服务、其余 `.../api/*` 转给业务后端，前端无需感知分流。

## 5. 运行测试

```bash
cd backend
pytest -v
```

## 6. 本地开发联调

没有公网域名、没有微信环境也能完整开发本平台的核心业务：

### 降级模式（无微信参数）

若 `.env` 未填写微信参数，系统自动进入**开发降级模式**：

- 消息回调 / OAuth / 模板消息相关接口不会真正调用微信
- 通知内容打印到后端日志（而非真正推送）
- 可用 `POST /api/auth/dev-login` 以任意角色获取 JWT，联调全部业务流程

```bash
# 以工程师角色快速登录拿 token（仅开发环境可用）
curl -X POST http://127.0.0.1:8000/api/auth/dev-login \
  -H "Content-Type: application/json" \
  -d '{"nickname":"测试工程师","role":"engineer"}'
```

这样在没有微信服务号的情况下也能完整开发、演示工单全流程。

### 内网穿透（需要真机微信联调时）

要在微信里真机测试（消息回调、OAuth、菜单跳转），需把本机 8000 端口暴露到公网 HTTPS。可用 frp / ngrok / cpolar 等工具：

```bash
# 以 cpolar 为例
cpolar http 8000
# 得到形如 https://xxxx.cpolar.io 的公网地址，
# 填入微信公众平台「服务器配置 URL」和 .env 的 WECHAT_OAUTH_REDIRECT
```

微信服务号的正式对接步骤见 [WECHAT.md](./WECHAT.md)。

## 7. 生产部署建议

- 后端用 `gunicorn -k uvicorn.workers.UvicornWorker app.main:app` 多进程运行
- 前置 Nginx 反向代理，配置 HTTPS（微信服务号要求 HTTPS）
- 微信服务器配置 URL 指向 `https://your.domain/api/wechat/callback`
- 前端 `npm run build` 后由 Nginx 托管 `dist/`
