# 代码结构总览（CODEBASE_OVERVIEW）

> 本文是对 `backend/` 后端**实际代码**的结构与功能梳理（2026-07-18 基于代码库生成）。
> 设计蓝图见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)，集成设计见 [INTEGRATION_DESIGN.md](INTEGRATION_DESIGN.md)，团队分工见 [TEAM.md](../docs/TEAM.md)。
> 关系：**ARCHITECTURE = 总体架构目标，INTEGRATION_DESIGN = 外部集成设计，CODEBASE_OVERVIEW = 代码现状，TEAM = 协作约定**。

---

## 一、项目定位

面向**工业移动机器人（AGV/AMR）**的智能客服与工单系统。以**微信公众号服务号**为入口，对外提供「我要摇人（报障咨询）/ 系统任务（处理）/ 后台管理」三类视角，内嵌 AI 问答与外部任务源集成（禅道）。代码注释与文档表明它由内部多个历史微服务（AAS 认证 / DAS 交付 / FQA 工单 / WeChat）**收敛重构为单一 FastAPI 应用**。

## 二、技术栈

| 类别 | 选型 |
|---|---|
| Web 框架 | FastAPI 0.111 + Uvicorn（`main.py` 仅 `uvicorn.run("app:app", port=8400)`） |
| ORM / DB | SQLAlchemy 2.0（同步 `pymysql`；异步优先 `asyncmy`，缺失时回退 `aiomysql`——Py3.14 下 asyncmy 无 wheel 且编译失败）+ MySQL 8（**≥8.0.14**，避开 8.0.13 的 `DEFAULT(now())` 建 index bug） |
| 迁移 | Alembic（URL 不写死，由 `env.py` 从 `settings.DB_CONFIG` 注入） |
| 缓存/队列 | Redis（缓存 + Celery broker/backend） |
| 异步任务 | Celery 5.6 |
| 对象存储 | MinIO（主） + 阿里云 OSS（CLI 分片上传脚本） |
| AI | OpenAI SDK 指向 **DeepSeek/Qwen/GLM/SiliconFlow** 兼容接口；MQTT(paho-mqtt) 备用通道 |
| 全文检索 | Meilisearch（可降级：`MEILI_ENABLED=False` 时回退 ilike） |
| 消息 | 微信公众平台 API（access_token / 菜单 / 标签 / 模板消息 / 加解密） |
| 认证 | JWT（python-jose）+ passlib（pbkdf2_sha256），RBAC |
| 外部集成 | 禅道（Zentao）任务源适配器，插件化设计 |
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
│   └── middleware/         #   timing.py（RequestResponseLogging，未注册）
├── app/models/             # ★ 全项目唯一 ORM 定义点（单一 Base.metadata）
│   ├── base.py             #   declarative_base()
│   ├── identity.py         #   users / roles / permissions / role_permissions / user_project_roles
│   ├── delivery.py         #   project / risk / project_daily_report / project_license + 采集表
│   ├── task.py             #   tasks / task_comments + TaskStatus/Priority/Type 枚举
│   ├── conversation.py     #   conversations / messages
│   ├── ticket.py           #   工单模型（兼容旧版）
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
│   │   ├── api/            #     管理端 API 路由
│   │   ├── schemas/        #     请求/响应数据结构
│   │   ├── services/       #     业务服务层
│   │   ├── resource_manager/ #  资源管理子模块（MinIO/OSS）
│   │   ├── models_das/     #     DAS 相关模型
│   │   ├── schemas_das/    #     DAS 请求模型
│   │   └── utils_das/      #     DAS 工具类（MQTT/日志/安全）
│   ├── tasks/              #   系统任务收件箱（工单 CRUD/状态机/派单/Celery 异步）
│   │   ├── api/            #     任务 API
│   │   ├── schemas/        #     数据结构
│   │   ├── services/       #     工单服务
│   │   ├── services_async/ #     异步任务服务
│   │   ├── tasks.py        #     Celery 异步任务定义
│   │   └── queue.py        #     任务队列配置
│   └── call/               #   我要摇人（AI 问答/会话/消息/我的工单）
│       ├── api/            #     对话 API（qa/conversations/messages/my-tasks/diagnosis）
│       ├── models/         #     对话模型（shim）
│       ├── schemas/        #     数据结构
│       ├── schemas_user/   #     用户相关结构
│       ├── services/       #     对话/消息/模型服务
│       └── services_user/  #     用户服务
├── app/integrations/       # ★ 外部任务源集成（插件化设计，见 INTEGRATION_DESIGN.md）
│   ├── api.py              #   集成源 API 路由
│   ├── base.py             #   集成基类定义
│   ├── engine.py           #   集成引擎
│   ├── registry.py         #   任务源注册表
│   ├── mappings_api.py     #   映射配置 API
│   └── sources/            #   具体任务源实现
│       └── zentao/         #     禅道集成（adapter/client/mapper）
├── app/wechat/             # 微信公众号外壳（OAuth/消息回调/菜单/标签/通知/JS-SDK）
│   ├── api/                #   微信 API 路由
│   ├── services/           #   微信业务服务
│   ├── schemas/            #   微信数据结构
│   ├── templates/          #   HTML 模板（AGV 效率报告等）
│   └── utils/              #   工具类（消息加解密/日志/二维码）
├── alembic/                # env.py + versions/（0001_baseline、wave2 ticket→task、task_source）
├── tests/                  # 集成测试（adapter/engine/mapper）
├── docs/                   # 项目文档（sample_manual.md）
├── INTEGRATION_DESIGN.md   # 外部任务源集成设计文档
├── MIGRATION.md            # 迁移记录
└── requirements.txt
```

**模块内部统一遵循 `api（路由）→ services（业务）→ models/schemas（数据契约）` 三层结构**；`modules/*/models/` 下全是再导出 shim，指向 `app/models/`。

### 核心模块速览

| 模块 | 视角 | 路由前缀 | 核心路由 | 主要 Service |
|---|---|---|---|---|
| `modules/admin` | 管理 | `/api/admin` | projects / risks / daily-reports / export / users / roles / permissions / resource-manager | AuthService、ProjectService、RiskService、DailyReportService、PermissionChecker、ResourceService |
| `modules/tasks` | 供给（处理方） | `/api/tasks` | task CRUD + 状态/派单/AI 分配 + comments/attachments + assignable-users（可指派人员选人，仅登录、字段最小化）+ Celery 异步 + 评论实时 WebSocket（`/{id}/ws`，发布-订阅） | TicketService、TaskService（异步） |
| `modules/call` | 需求（请求方） | `/api/call` | qa（AI 问答）/ conversations / messages / my-tasks / diagnosis | **ModelService（AI 核心）**、ConversationService、MessageService |
| `integrations` | 外部集成 | `/api/tasks/sources` | 任务源列表、映射配置 | Engine、Registry、ZentaoAdapter |
| `wechat` | 微信入口 | `/api/wechat` | OAuth 登录、消息回调、菜单/标签、通知、JS-SDK | WechatService、AuthService、DataService、ProjectTicketService |

> **call 会话持久化**：`POST /api/call/conversations` 的 `user_id` **按 token 覆盖**（前端只持 `username`，须与列表查询的 `current_user.id` 对齐）。AI 诊断流式（`/api/ai/*`）本身走 Redis 短期记忆、**不写** `conversations/messages` 表——前端 `ChatPanel` 自行把每轮 user/assistant 消息落库以实现历史持久化与刷新恢复。注意 `SceneType` 枚举只有 `chat/faq/support/consultation/other`（无 `task_assist`，前端 tasks 场景映射为 `consultation`）。

## 四、分层与请求流

```
微信/H5 ──HTTPS──> FastAPI app (__init__.py)
                    │  CORS 中间件（唯一已注册）
                    │  startup: 注入 OpenAPI JWT scheme + init_users_db()
                    ▼
   ┌───────────────────────────────────────────────────────┐
   │ 6 个顶层路由（均挂 /api 下）                             │
   │  /auth   /admin/*   /tasks/*   /call/*   /wechat/*      │
   │  /tasks/sources（integrations）                          │
   └───────────────────────────────────────────────────────┘
                    │ Depends(require_permission) 逐路由鉴权
                    ▼
              modules/*/services（业务逻辑）
                    │
        ┌───────────┼────────────┬──────────────┐
        ▼           ▼            ▼              ▼
   app/models    app/services   外部服务      基础设施
   (ORM/MySQL)  (identity/perm)  (OpenAI/      (MinIO/Redis/
                                 MQTT/微信/     Celery/
                                 禅道)         Meilisearch)
```

## 五、核心设计要点

1. **单一 ORM 归属**：`app/models/base.py` 是全项目唯一 `declarative_base()`，保证 Alembic autogenerate 与跨模块外键正确。所有 shim 都回指它。
2. **RBAC 鉴权**：`core/auth_routes.py` 的 `require_permission(perm, project_id)` 支持 admin 直通、`resource:*` 通配、按 project 维度的角色聚合（`PermissionService.get_user_with_roles` 合并直通+各角色权限 → `projectPermissions`）。
3. **任务状态机**：`tasks` 表用 `TaskStatus` 枚举（new/in_progress/pending/resolved/closed）+ `ALLOWED_TRANSITIONS` 强制流转；工单（ticket）已升格为统一"任务"，保留 `Ticket = Task` 别名兼容。
4. **认证流程**：微信 OAuth 换 openid → 查建用户 → 签 JWT；开发环境可用 dev-login 直登。双 token（access 30min / refresh 7day）。
5. **外部任务源集成**：`app/integrations/` 采用插件化设计，通过 `TASK_SOURCES_ENABLED` 配置启用（如 `["zentao"]`），支持任务源与本地任务的映射。
6. **通知**：`utils/notification_utils.py` 用线程池异步发 MQTT + 微信模板消息（派单/新讨论/上报）。
7. **配置防呆**：`APP_ENV=production` 时 config 强制校验 DB/SECRET/微信凭证非空。
8. **全文检索**：支持 Meilisearch，可降级为数据库 ilike 查询。
9. **评论区实时 WebSocket**：`modules/tasks/api/ws.py` 以 FastAPI 原生 WebSocket 实现「发布-订阅」式评论实时推送（presence / typing / read_receipt / task.updated），内存房间模型（单进程够用，多实例扩展见设计文档 §9），已读游标持久化到 `task_comment_read` 表；REST 评论/状态接口写库成功后调用 `ws_broadcast_*` 广播。

## 六、数据模型（19 张表，分 5 域）

| 域 | 表 | 来源 |
|---|---|---|
| 身份与权限 | users / roles / permissions / role_permissions / user_project_roles（含 `report_to_id` 汇报链） | AAS |
| 项目交付 | project / risk / project_daily_report / project_license / realtime_data / history_data / collection_data | DAS |
| 任务工单 | tasks / task_comments / task_comment_read | HelpDesk（ticket 升格）+ 实时已读游标（WS） |
| 咨询对话 | conversations / messages | AI 问询 |
| 资源 | resources / resource_folders | HelpDesk resource_manager |

## 七、跨模块外部依赖速查

| 依赖 | 使用位置 |
|---|---|
| OpenAI SDK（DeepSeek/Qwen/GLM/SiliconFlow） | `call/services/model_service.py` |
| MQTT (paho-mqtt) | `call/services/model_service.py`、`admin/utils_das/mqtt.py` |
| Celery + Redis | `tasks/queue.py`、`tasks/tasks.py`、`tasks/services_async/task_service.py` |
| MinIO | `admin/resource_manager/*`、`tasks/services/ticket_service.py`（附件） |
| 阿里云 OSS | `admin/resource_manager/api/aliyun_oss/oss_main.py` |
| 微信公众平台 API | `wechat/services/wechat_service.py`、`wechat/api/*` |
| Meilisearch | 全文检索（通过配置启用） |
| 禅道 API | `integrations/sources/zentao/client.py` |

## 八、规划 vs 现状

`ARCHITECTURE.md` 和 `INTEGRATION_DESIGN.md` 描绘的蓝图与当前代码的对比：

| 蓝图项 | 现状 |
|---|---|
| 外部任务源集成（禅道） | ✅ 已实现（`app/integrations/sources/zentao/`） |
| Qdrant 向量库 + 知识库 RAG | ✅ 已实现（独立 `ai/` 模块，含 ingestion/、kb/qdrant/、embed_models/） |
| AI Agent（数据分析/诊断） | ✅ 已实现（`ai/agents/AiDataAnalysisPlatform/`、`AiDiagnosisPlatform/`） |
| AI 多提供商支持 | ✅ 已实现（DeepSeek/Qwen/GLM/SiliconFlow/OpenAI 兼容） |
| Meilisearch 全文检索 | ✅ 已实现（可配置启用） |
| Celery 异步任务 | ✅ 已实现（任务派发、异步处理） |
| 评论区实时 WebSocket（轻量 IM） | ✅ 已实现（`tasks/api/ws.py`：评论 CRUD 实时 + 在线状态 + 输入中 + 已读回执 + 工单状态推送） |

**已稳固落地的能力**：RBAC 账号体系、统一任务/工单状态机、DAS 项目交付管理、微信公众号外壳（OAuth/菜单/标签/通知）、MinIO 资源管理、Celery 异步任务、禅道任务源集成、多提供商 AI 问答、Qdrant 向量知识库、AI Agent（数据分析/诊断）。

## 九、值得关注的工程债

1. **建表双轨**：`core/database.py` 导入即 `Base.metadata.create_all`，与 Alembic 并行——注释说 Alembic 是目标态，运行时仍 create_all 兜底。
2. **全局 JWT 中间件未启用**：`PermissionMiddleware` 写好但未 `add_middleware`，实际鉴权全靠逐路由 `Depends`。
3. **shim 层冗余**：`modules/*/models/` 下大量再导出文件是迁移期产物，是后续清理对象。
4. **重复实现**：`hmac_utils.py` 与 `core/security.py` 有重复的密码哈希；`admin/utils_das/logging.py` 是 `services/logging.py` 的逐字拷贝。
5. **`setup_logging()` 从未被调用**，日志实际依赖 root logger 默认行为。
6. **配置双套并存**：AI 配置同时有旧 `AI_*` 与新 `LLM_*`，靠 validator 互补。
7. **路由注册顺序敏感**：`integrations_sources_router` 须在 `tasks_router` 之前注册，否则 `GET /tasks/sources` 会被 `GET /tasks/{task_id}` 贪婪匹配吞掉。

---

## 十、一句话概括

这是一个**架构设计完整、底座（RBAC + 任务工单 + 微信外壳 + 交付管理 + 禅道集成）已落地、AI 能力（多提供商问答 + Qdrant 知识库 + AI Agent）已实现**的 FastAPI 单体后端。外部任务源集成采用插件化设计，可扩展支持更多任务管理系统。项目采用前后端分离（`frontend/`）+ AI 独立服务（`ai/`）的多模块架构。
