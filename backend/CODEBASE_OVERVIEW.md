# 代码结构总览（CODEBASE_OVERVIEW）

> 本文是对 `backend/` 后端**实际代码**的结构与功能梳理（2026-07-14 基于代码库生成）。
> 设计蓝图见 [ARCHITECTURE.md](./ARCHITECTURE.md)，团队分工见 [TEAM.md](./TEAM.md)。
> 三者关系：**ARCHITECTURE = 目标设计，CODEBASE_OVERVIEW = 代码现状，TEAM = 协作约定**。

---

## 一、项目定位

面向**工业移动机器人（AGV/AMR）**的智能客服与工单系统。以**微信公众号服务号**为入口，对外提供「我要摇人（报障咨询）/ 系统任务（处理）/ 后台管理」三类视角，内嵌 AI 问答与（规划中的）知识库 RAG。代码注释与文档表明它由内部多个历史微服务（AAS 认证 / DAS 交付 / FQA 工单 / WeChat）**收敛重构为单一 FastAPI 应用**。

## 二、技术栈

| 类别 | 选型 |
|---|---|
| Web 框架 | FastAPI 0.111 + Uvicorn（`main.py` 仅 `uvicorn.run("app:app", port=8400)`） |
| ORM / DB | SQLAlchemy 2.0（同步 `pymysql` + 异步 `asyncmy`）+ MySQL 8 |
| 迁移 | Alembic（URL 不写死，由 `env.py` 从 `settings.DB_CONFIG` 注入） |
| 缓存/队列 | Redis（缓存 + Celery broker/backend） |
| 异步任务 | Celery 5.6 |
| 对象存储 | MinIO（主） + 阿里云 OSS（CLI 分片上传脚本） |
| AI | OpenAI SDK 指向 **DeepSeek** 兼容接口；MQTT(paho-mqtt) 备用通道 |
| 消息 | 微信公众平台 API（access_token / 菜单 / 标签 / 模板消息 / 加解密） |
| 认证 | JWT（python-jose）+ passlib（pbkdf2_sha256），RBAC |
| 文档处理 | pdfplumber / pymupdf / python-docx / pdf2docx / pypandoc / opencv / pillow（服务于资源/知识库素材） |

## 三、目录结构与职责

```
backend/
├── main.py                 # 进程入口：uvicorn 启动
├── app/__init__.py         # ★ FastAPI app 实例 + 路由装配 + 启动事件
├── app/core/               # 基础设施层（配置/DB/安全/认证/中间件）
│   ├── config.py           #   pydantic-settings 单例 settings，读 .env
│   ├── db.py               #   ★ 真正的同步/异步 engine + 会话工厂 + get_db()
│   ├── database.py         #   DatabaseManager 门面 + init_users_db()（导入即 create_all）
│   ├── security.py         #   JWT 签发/校验 + 密码哈希
│   ├── auth_service.py     #   AuthService（login/refresh/me）
│   ├── auth_routes.py      #   /auth 路由 + 核心鉴权依赖 require_permission()
│   └── middleware/         #   timing.py（RequestResponseLogging + PermissionMiddleware，未注册）
├── app/models/             # ★ 全项目唯一 ORM 定义点（单一 Base.metadata）
│   ├── base.py             #   declarative_base()
│   ├── identity.py         #   users / roles / permissions / role_permissions / user_project_roles
│   ├── delivery.py         #   project / risk / project_daily_report / project_license + 采集表
│   ├── task.py             #   tasks / task_comments + TaskStatus/Priority/Type 枚举
│   ├── conversation.py     #   conversations / messages
│   └── resource.py         #   resources / resource_folders
├── app/schemas/            # 顶层 Pydantic 模型（user/token/role/project/permission/response）
├── app/services/           # 跨模块共享服务
│   ├── identity_service.py #   用户/角色/权限/项目 CRUD（db_manager 的真实后端）
│   ├── permission_service.py#  角色权限聚合 → projectPermissions
│   ├── user_service.py     #   带 600s 内存缓存的用户查询
│   ├── logging.py          #   dictConfig（注意：从未被调用）
│   └── hmac_utils.py       #   HMAC/pinyin 工具（含重复的密码哈希）
├── app/utils/              # minio_client / notification(MQTT+模板消息) / image_processor / database_utils
├── app/modules/            # ★ 三大业务模块（垂直切分）
│   ├── admin/              #   后台管理（看板/项目/风险/日报/授权/用户/角色/权限/资源管理）
│   ├── tasks/              #   系统任务收件箱（工单 CRUD/状态机/派单/Celery 异步）
│   └── call/               #   我要摇人（AI 问答/会话/消息/我的工单）
├── app/wechat/             # 微信公众号外壳（OAuth/消息回调/菜单/标签/通知/JS-SDK）
├── app/ai/agents/          # ★ 规划的三 Agent —— 当前为空
├── app/kb/                 # ★ 规划的知识库 —— 当前为空
├── alembic/                # env.py + versions/（0001_baseline、wave2 ticket→task）
├── tests/                  # 几乎为空（仅一行 print）
├── docker-compose.yml      # MySQL + Redis + MinIO + app
├── ARCHITECTURE.md / TEAM.md / README.md
└── requirements.txt
```

**模块内部统一遵循 `api（路由）→ services（业务）→ models/schemas（数据契约）` 三层结构**；`modules/*/models/` 下全是再导出 shim，指向 `app/models/`。

### 三大业务模块速览

| 模块 | 视角 | 路由前缀 | 核心路由 | 主要 Service |
|---|---|---|---|---|
| `modules/admin` | 管理 | `/api/admin` | projects / risks / daily-reports / export / users / roles / permissions / resource-manager | AuthService、ProjectService、RiskService、DailyReportService、PermissionChecker、ResourceService |
| `modules/tasks` | 供给（处理方） | `/api/tasks` | task CRUD + 状态/派单/AI 分配 + comments/attachments + Celery 异步 | TicketService、TaskService（异步） |
| `modules/call` | 需求（请求方） | `/api/call` | qa（AI 问答）/ conversations / messages / my-tasks | **ModelService（AI 核心）**、ConversationService、MessageService |

`app/wechat/`（前缀 `/api/wechat`）：微信消息回调、OAuth 登录、菜单/标签管理、模板与链接消息通知、JS-SDK 配置、健康检查。Service 含 `WechatService`（access_token/jsapi 缓存）、`AuthService`、`DataService`、`ProjectTicketService`。

## 四、分层与请求流

```
微信/H5 ──HTTPS──> FastAPI app (__init__.py)
                    │  CORS 中间件（唯一已注册）
                    │  startup: 注入 OpenAPI JWT scheme + init_users_db()
                    ▼
   ┌───────────────────────────────────────────────┐
   │ 5 个顶层路由（均挂 /api 下）                     │
   │  /auth   /admin/*   /tasks/*   /call/*   /wechat/* │
   └───────────────────────────────────────────────┘
                    │ Depends(require_permission) 逐路由鉴权
                    ▼
              modules/*/services（业务逻辑）
                    │
        ┌───────────┼────────────┬──────────────┐
        ▼           ▼            ▼              ▼
   app/models    app/services   外部服务      基础设施
   (ORM/MySQL)  (identity/perm)  (OpenAI/      (MinIO/Redis/
                                 MQTT/微信)    Celery)
```

## 五、核心设计要点

1. **单一 ORM 归属**：`app/models/base.py` 是全项目唯一 `declarative_base()`，保证 Alembic autogenerate 与跨模块外键正确。所有 shim 都回指它。
2. **RBAC 鉴权**：`core/auth_routes.py` 的 `require_permission(perm, project_id)` 支持 admin 直通、`resource:*` 通配、按 project 维度的角色聚合（`PermissionService.get_user_with_roles` 合并直通+各角色权限 → `projectPermissions`）。
3. **任务状态机**：`tasks` 表用 `TaskStatus` 枚举（new/in_progress/pending/resolved/closed）+ `ALLOWED_TRANSITIONS` 强制流转；工单（ticket）已升格为统一"任务"，保留 `Ticket = Task` 别名兼容。
4. **认证流程**：微信 OAuth 换 openid → 查建用户 → 签 JWT；开发环境可用 dev-login 直登。双 token（access 30min / refresh 7day）。
5. **USP 接缝**：与 USP 调度平台的边界是机器对机器的入站数据接口（`POST /api/integration/usp/faults`、`/task-stats`），独立鉴权，落 `robot_faults`/`robot_task_stats`。
6. **通知**：`utils/notification_utils.py` 用线程池异步发 MQTT + 微信模板消息（派单/新讨论/上报）。
7. **配置防呆**：`APP_ENV=production` 时 config 强制校验 DB/SECRET/微信凭证非空。

## 六、数据模型（19 张表，分 5 域）

| 域 | 表 | 来源 |
|---|---|---|
| 身份与权限 | users / roles / permissions / role_permissions / user_project_roles（含 `report_to_id` 汇报链） | AAS |
| 项目交付 | project / risk / project_daily_report / project_license / realtime_data / history_data / collection_data | DAS |
| 任务工单 | tasks / task_comments | HelpDesk（ticket 升格） |
| 咨询对话 | conversations / messages | AI 问询 |
| 资源 | resources / resource_folders | HelpDesk resource_manager |

## 七、跨模块外部依赖速查

| 依赖 | 使用位置 |
|---|---|
| OpenAI SDK / DeepSeek（`LLM_API_KEY`/`LLM_API_URL`） | `call/services/model_service.py` |
| MQTT (paho-mqtt) | `call/services/model_service.py`、`admin/utils_das/mqtt.py` |
| Celery + Redis | `tasks/queue.py`、`tasks/tasks.py`、`tasks/services_async/task_service.py` |
| MinIO（`app.utils.minio_client`） | `admin/resource_manager/*`、`tasks/services/ticket_service.py`（附件） |
| 阿里云 OSS | `admin/resource_manager/api/aliyun_oss/oss_main.py` |
| 微信公众平台 API | `wechat/services/wechat_service.py`、`wechat/api/*` |
| HTTP 外调 DAS/AAS | `wechat/services/*`、`admin/services/wechat_service.py`（`usp.ep-zl.com`） |

## 八、规划 vs 现状（重要）

`ARCHITECTURE.md` 描绘的蓝图与当前代码有显著落差，**已落地的是底座，未落地的是上层 AI 能力**：

| 蓝图项 | 现状 |
|---|---|
| Qdrant 向量库 + 五层知识库 RAG | `app/kb/` **空目录**；docker-compose 无 Qdrant；requirements 无向量库客户端 |
| 三视角三 Agent | `app/ai/agents/` **空目录**；实际 AI 仅是 `call/services/model_service.py` 的 `ModelService`（OpenAI+MQTT 双通道函数调用，未抽象成 Agent） |
| Redis 队列 | 已用于 Celery，但 AI 队列化未实现 |
| Nginx / gunicorn 多进程 | compose 中未见 Nginx 服务 |

**已稳固落地的能力**：RBAC 账号体系、统一任务/工单状态机、DAS 项目交付管理、微信公众号外壳（OAuth/菜单/标签/通知）、MinIO 资源管理、Celery 异步任务骨架。

## 九、值得关注的工程债

1. **建表双轨**：`core/database.py` 导入即 `Base.metadata.create_all`，与 Alembic 并行——注释说 Alembic 是目标态，运行时仍 create_all 兜底。
2. **全局 JWT 中间件未启用**：`PermissionMiddleware` 写好但未 `add_middleware`（且 skip 路由还是旧 `/AAS/...` 前缀），实际鉴权全靠逐路由 `Depends`。
3. **shim 层冗余**：`modules/*/models/` 下大量再导出文件是迁移期产物，是后续清理对象。
4. **重复实现**：`hmac_utils.py` 与 `core/security.py` 有重复的密码哈希；`admin/utils_das/logging.py` 是 `services/logging.py` 的逐字拷贝。
5. **`setup_logging()` 从未被调用**，日志实际依赖 root logger 默认行为。
6. **无自动化测试**：`tests/` 仅一行 print，无 conftest/fixture/真实用例。
7. **配置双套并存**：AI 配置同时有旧 `AI_*` 与新 `LLM_*`，靠 validator 互补。

---

## 十、一句话概括

这是一个**架构设计完整、底座（RBAC + 任务工单 + 微信外壳 + 交付管理）已落地、但上层 AI/知识库尚未实现**的 FastAPI 单体后端，正处于从「多微服务收敛统一」的迁移收尾期（`MIGRATION.md` Wave 1/2）。文档（ARCHITECTURE/TEAM）质量很高，可作为后续知识库与三 Agent 的实施蓝图。
