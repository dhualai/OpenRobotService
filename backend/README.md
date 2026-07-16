# 摇人吧 · OpenRobotService（后端）

面向**工业移动机器人（AGV/AMR）**的智能客服与工单系统后端。以微信公众号服务号为入口，提供「我要摇人 / 系统任务 / 后台管理」三类视角，内嵌 AI 问答与（规划中的）知识库 RAG。由内部多个历史微服务（AAS 认证 / DAS 交付 / FQA 工单 / WeChat）收敛为**单一 FastAPI 应用**。

> 单体后端 + 单 H5 前端，本仓为 `backend/`。

---

## 📚 文档导航

| 文档 | 内容 |
|---|---|
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | 目标设计蓝图：总体架构、五大模块、数据模型、状态机、认证流程、部署形态 |
| **[CODEBASE_OVERVIEW.md](./CODEBASE_OVERVIEW.md)** | 代码现状梳理：实际目录结构、分层、19 张表、规划 vs 现状、工程债 |
| **[TEAM.md](./TEAM.md)** | 团队组织、协作模式（100% AI 生成代码 + 垂直切分）、分工 |

> 先看本文上手，理解设计读 `ARCHITECTURE.md`，理解代码现状读 `CODEBASE_OVERVIEW.md`。

---

## ✨ 核心特性

- **三类业务视角**：`call`（我要摇人：报障 + AI 咨询）、`tasks`（系统任务：统一收件箱 + 状态机 + 派单）、`admin`（后台管理：项目/风险/日报/授权/用户/角色/资源）
- **RBAC 权限**：基于 `resource_type + action` 的细粒度权限，按项目维度聚合角色
- **微信公众号外壳**：OAuth 登录、消息回调加解密、菜单/标签管理、模板消息通知、JS-SDK
- **AI 问答**：OpenAI SDK 指向 DeepSeek 兼容接口，支持流式；MQTT 备用通道
- **异步任务**：Celery + Redis（任务处理、催办通知）
- **对象存储**：MinIO（资源/附件）+ 阿里云 OSS 上传脚本

## 🛠 技术栈

| 类别 | 选型 |
|---|---|
| Web | FastAPI 0.111 + Uvicorn |
| ORM/DB | SQLAlchemy 2.0（同步 pymysql + 异步 asyncmy）+ MySQL 8 |
| 迁移 | Alembic |
| 缓存/队列 | Redis |
| 异步任务 | Celery |
| 存储 | MinIO（+ 阿里云 OSS） |
| AI | OpenAI SDK（DeepSeek 兼容）+ MQTT |
| 认证 | JWT（python-jose）+ passlib |

---

## 🚀 快速开始

### 环境要求

- **Python 3.12**
- **MySQL 8**、**Redis**、**MinIO**（本地安装，或用下面的 Docker Compose 一键起）
- 微信凭证、AI 服务地址、MQTT（均**可选**，留空自动进入开发降级模式）

### 1. 安装依赖

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

按需修改 `.env`，关键字段：

| 字段 | 说明 |
|---|---|
| `DATABASE_URL` | `mysql+pymysql://用户:密码@主机:端口/库?charset=utf8mb4` |
| `SECRET_KEY` / `JWT_SECRET` | 生产环境**必填**，请改成随机长串 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 首次启动自动创建的管理员账号（默认 `admin / 123456`） |
| `REDIS_*` / `MINIO_*` | 缓存与对象存储连接 |
| `WECHAT_*` | 微信服务号凭证（留空降级为日志输出） |
| `AI_MODEL_URL` / `LLM_*` | AI 服务地址与密钥 |
| `APP_ENV` | `dev` 或 `production`；生产模式强制校验关键配置 |

完整字段见 [.env.example](./.env.example)。

### 3. 初始化数据库

```bash
# 全新数据库：执行迁移建表
alembic upgrade head

# 已有旧库（表已存在）：标记到当前版本，不走迁移
alembic stamp head
```

> 说明：`app/core/database.py` 在导入时会 `Base.metadata.create_all` 兜底建表，Alembic 是目标态的版本化迁移。新环境推荐用 Alembic。

### 4. 运行

**方式一：启动脚本（推荐，本地开发）**

```bash
./start.sh          # Linux / macOS
start.bat           # Windows
```

脚本会激活 `.venv` 并运行 `uvicorn app:app --host 0.0.0.0 --port 8000 --reload`。

**方式二：直接 uvicorn**

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

**方式三：Docker Compose（一键起 MySQL + Redis + MinIO + 后端）**

```bash
docker-compose up -d
```

### 5. 访问

| 入口 | 地址 |
|---|---|
| API 文档（Swagger） | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| 健康检查 | http://localhost:8000/api/health |
| 默认管理员 | `admin / 123456` |

> 端口默认 `8000`（由 `.env` 的 `API_PORT` 控制）。

---

## 📂 项目结构（精简版）

```
backend/
├── main.py / start.sh / start.bat   # 启动入口
├── app/
│   ├── __init__.py          # ★ FastAPI app 实例 + 路由装配
│   ├── core/                # 基础设施：config / db / security / auth
│   ├── models/              # ★ 全项目唯一 ORM 定义（19 张表）
│   ├── schemas/             # Pydantic 契约
│   ├── services/            # 跨模块共享服务（identity / permission / user）
│   ├── utils/               # minio / notification / image 处理
│   ├── modules/             # 三大业务模块
│   │   ├── admin/           #   后台管理
│   │   ├── tasks/           #   系统任务（含 Celery）
│   │   └── call/            #   我要摇人（AI 问答）
│   ├── wechat/              # 微信公众号外壳
│   ├── ai/agents/           # 三 Agent（规划中）
│   └── kb/                  # 知识库（规划中）
├── alembic/                 # 数据库迁移
├── tests/                   # 测试（待补）
└── docker-compose.yml
```

完整结构与职责见 [CODEBASE_OVERVIEW.md](./CODEBASE_OVERVIEW.md)。

## 🔌 主要 API 模块

所有业务路由统一挂在 `/api` 下：

| 前缀 | 模块 | 说明 |
|---|---|---|
| `/api/auth` | 认证 | 登录 / 刷新 / 当前用户 / 开发登录 |
| `/api/admin/*` | 后台管理 | 项目 / 风险 / 日报 / 授权 / 用户 / 角色 / 权限 / 资源管理 |
| `/api/tasks/*` | 系统任务 | 工单 CRUD / 状态机 / 派单 / 评论附件 / 异步任务 |
| `/api/call/*` | 我要摇人 | AI 问答 / 会话 / 消息 / 我的工单 |
| `/api/wechat/*` | 微信 | 消息回调 / OAuth / 菜单 / 标签 / 通知 |

---

## 🧪 测试

当前 `tests/` 仅有占位用例，自动化测试体系待补：

```bash
pytest
```

## 📌 现状提示

- **底座已落地**：RBAC、任务工单状态机、DAS 交付管理、微信外壳、MinIO 资源管理、Celery 骨架。
- **上层待建**：知识库（Qdrant RAG）、三 Agent 抽象 —— `app/ai/` 与 `app/kb/` 目前为空占位目录。
- 项目处于「多微服务收敛统一」迁移收尾期，部分 `modules/*/models/` 为兼容 shim。

详见 [CODEBASE_OVERVIEW.md](./CODEBASE_OVERVIEW.md) 的「规划 vs 现状」「工程债」两节。
