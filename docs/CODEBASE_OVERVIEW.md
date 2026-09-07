# 代码结构总览（仓库级 CODEBASE_OVERVIEW）

> 本文对 OpenRobotService 仓库**全部代码**（backend / frontend / ai / automation / dags / deploy / scripts）的结构与功能做整体梳理（2026-09-01 基于代码库与既有文档生成）。
> 相关文档索引：
> - 产品形态：`docs/PRODUCT/PRODUCT.md`；技术设计蓝图：`docs/PRODUCT/ARCHITECTURE.md`
> - 后端代码现状（模块级细节）：`backend/CODEBASE_OVERVIEW.md`
> - 后端 API 清单与权限分析：`backend/API.md`；外部任务源集成设计：`backend/INTEGRATION_DESIGN.md`
> - AI 服务接口文档：`ai/AI_Service_Description.md`
> - 前端工程文档（含全部维护历史）：`frontend/项目文档.md`（本地文件，已 gitignore）
> - 自动化测试平台：`automation/README.md`
> - 测试视角架构说明：`docs/project_architecture.md`

---

## 一、项目定位

**OpenRobotService**（公共实例：**「摇人吧」**）是面向工业移动机器人（AGV/AMR）项目交付场景的微信服务号平台：现场遇到问题 → 微信里 AI 咨询 / 提交工单 → 自动派给对应工程师 / 项目经理 → 工单内协作讨论 → 疑难一键上报 → 闭环解决，并把处理经验沉淀进知识库。

不仅是一个工单系统，还覆盖**项目交付管理**（项目 / 风险 / 日报 / 授权 / 人员画像）与**机器人运行数据**（USP 调度平台上传统计），由 **AI 沿"需求 / 供给 / 管理"三视角全流程参与**，配合**专业知识库（RAG）**提升响应效率。

代码形态：**前后端分离 + AI 独立服务**的单体仓库，三个独立服务：

| 服务 | 入口 | 端口 | 职责 |
|------|------|------|------|
| 业务后端 | `backend/main.py` | 8400 | `/api/auth \| admin \| tasks \| call \| wechat \| tasks/sources` |
| AI 服务 | `ai/run.py` | 8401 | `/api/ai/*`（问答 / 对话 / 记忆 / 任务 Agent / 企业微信 / 数据分析） |
| 前端 | `frontend/` | 5173（dev） | React H5（微信内打开），`/api/*` 经 nginx/vite 代理分流到两个后端 |

---

## 二、顶层目录结构

```
OpenRobotService/
├── backend/           # 业务后端（FastAPI 单体，Python 3.11+ / MySQL 8）
├── frontend/          # 前端（Vite 7 + React 19 + TS 5.9 strict + TDesign Mobile 0.23）
├── ai/                # AI 独立服务（FastAPI @8401：三视角 Agent + RAG + 会话记忆）
├── automation/        # 数据驱动自动化测试平台（Excel 用例 + Mock 后端 + pytest）
├── dags/              # Airflow DAG（工单截止预警 + 逾期通知）
├── deploy/            # 部署配置（nginx 按环境前缀分发）
├── scripts/           # 运维/迁移脚本（用户导入、时区迁移、DB 初始化）
├── docs/              # 文档中心（产品 / 架构 / 设计 / 方案）
├── app/  co/          # 边缘目录（app/kb 等，非核心）
└── README.md          # 项目入口说明（产品 + 快速开始）
```

---

## 三、后端 `backend/`（FastAPI 业务后端）

### 3.1 技术栈

| 类别 | 选型 |
|---|---|
| Web 框架 | FastAPI 0.111 + Uvicorn（`main.py` 仅 `uvicorn.run("app:app", port=8400)`） |
| ORM / DB | SQLAlchemy 2.0（同步 pymysql；异步 asyncmy 缺失时回退 aiomysql）+ MySQL 8（≥8.0.14） |
| 迁移 | Alembic（URL 由 `env.py` 从 `settings.DB_CONFIG` 注入） |
| 缓存/队列 | Redis（缓存 + Celery broker/backend）· Celery 5.6 |
| 对象存储 | MinIO（主）+ 阿里云 OSS |
| AI | OpenAI SDK 指向 DeepSeek/Qwen/GLM/SiliconFlow 兼容接口；MQTT 备用通道 |
| 全文检索 | Meilisearch（`MEILI_ENABLED=False` 时降级 ilike） |
| 消息 | 微信公众平台 API（access_token / 菜单 / 标签 / 模板消息 / 加解密） |
| 认证 | JWT（python-jose）+ passlib（pbkdf2_sha256），RBAC |
| 外部集成 | 禅道（Zentao）任务源适配器，插件化设计 |

### 3.2 分层架构

```
backend/
├── main.py                 # 进程入口
├── app/__init__.py         # ★ FastAPI app 实例 + 路由装配 + 启动事件（CORS 唯一中间件）
├── app/core/               # 基础设施层
│   ├── config.py           #   pydantic-settings 单例 settings（APP_ENV=production 强制校验）
│   ├── db.py               #   同步/异步 engine + 会话工厂 + get_db()
│   ├── database.py         #   DatabaseManager 门面 + init_users_db()（导入即 create_all 兜底）
│   ├── security.py         #   JWT 签发/校验 + 密码哈希
│   ├── auth_service.py     #   AuthService（login/refresh/me）
│   ├── auth_routes.py      #   /auth 路由 + 核心鉴权依赖 require_permission()
│   └── middleware/         #   timing.py（未注册）
├── app/models/             # ★ 全项目唯一 ORM 定义点（单一 Base.metadata）
│   ├── base.py             #   declarative_base()
│   ├── identity.py         #   users / roles / permissions / role_permissions / user_project_roles
│   ├── delivery.py         #   project / risk / project_daily_report / project_license + 采集表
│   ├── task.py             #   tasks / task_comments（+ task_comment_read 已读游标）
│   ├── task_dispatch_log.py#   task_dispatch_log（派单/重派日志）
│   ├── conversation.py     #   conversations / messages
│   ├── ticket.py           #   工单模型（兼容旧版）
│   ├── module_tree.py      #   产品→界面→功能 模块树（module_trees）
│   ├── module_tree_node.py #   功能节点（module_tree_nodes）
│   ├── module_tree_edit.py #   模块树协同编辑审批单
│   ├── organization.py     #   公司/部门组织架构（companies / departments）
│   └── resource.py         #   resources / resource_folders
├── app/schemas/            # 顶层 Pydantic 模型
├── app/services/           # 跨模块共享服务
│   ├── identity_service.py #   用户/角色/权限/项目 CRUD（db_manager 真实后端）
│   ├── permission_service.py # 角色权限聚合 → projectPermissions
│   ├── user_service.py     #   带 600s 内存缓存的用户查询
│   ├── redispatch_tip_service.py # 重派说明（派单说明气泡）服务
│   ├── logging.py / hmac_utils.py
├── app/utils/              # minio_client / notification_utils(MQTT+模板消息) / image_processor / data_utils / oss_client / database_init / migrate_org_data
├── app/modules/            # ★ 三大业务模块（垂直切分，api→services→schemas 三层）
│   ├── admin/              #   后台管理
│   ├── tasks/              #   系统任务收件箱
│   └── call/               #   我要摇人
├── app/integrations/       # 外部任务源集成（禅道，插件化）
├── app/wechat/             # 微信公众号外壳（OAuth/消息回调/菜单/标签/通知/JS-SDK）
├── alembic/                # 迁移（0001_baseline、wave2 ticket→task、task_source…）
├── dags/                   # ai_summarize_dag.py（AI 日报/周报总结 DAG）
├── tests/                  # pytest 集成测试
└── requirements.txt
```

### 3.3 三大业务模块速览

| 模块 | 视角 | 路由前缀 | 核心路由 | 主要 Service |
|---|---|---|---|---|
| `modules/admin` | 管理 | `/api/admin` | projects / risks / daily-reports / dashboard / export / users / roles / permissions / resource-manager / tickets（代理 AI）/ transport-efficiency / module-tree（+WS 协同编辑）/ data / user | AuthService、ProjectService、RiskService、DailyReportService、PermissionChecker、ResourceService、RedispatchTipService |
| `modules/tasks` | 供给（处理方） | `/api/tasks` | task CRUD + 状态机/派单 + comments/attachments + assignable-users + Celery 异步 + 评论实时 WebSocket（`/{id}/ws` 发布-订阅，presence/typing/read_receipt/task.updated） | TicketService、TaskService（异步） |
| `modules/call` | 需求（请求方） | `/api/call` | qa（AI 问答）/ conversations / messages / my-tasks / diagnosis / attachment（代理下载） | **ModelService（AI 核心）**、ConversationService、MessageService |
| `integrations` | 外部集成 | `/api/tasks/sources` | 任务源列表、映射配置 | Engine、Registry、ZentaoAdapter |
| `wechat` | 微信入口 | `/api/wechat` | OAuth 登录、消息回调、菜单/标签、通知、JS-SDK | WechatService、AuthService、DataService、PermissionService、ProjectTicketService、AiService |

### 3.4 关键业务功能（2026-08 新增亮点）

- **智能派单 / 重派（dispatch）**：`task_dispatch_log` 落派单日志；AI 端 Assigner（`ai/agents/AiDiagnosisPlatform/assigner.py`）精排推荐候选（倾向人/对接人保底 0.8 分、职级轻微打折），重派候选默认展示全部用户 + 画像，精排推荐仅含真有精排分者；派单说明（`redispatch_tip_service.py`）仅提单人可见。
- **模块树协同编辑（module-tree）**：产品→界面→功能树管理（`module_tree_ws.py` 提供 WebSocket 协同），编辑审批单（批准/驳回）、按行 id 并发安全的新增/更新/删除、各产品树版本哈希（乐观锁基准）+ 功能级冲突基准哈希。AI 派单据此选择召回目标（保存模块树/用户画像后调用 `/api/ai/assigner/reload` 热更新）。
- **搬运效率分析（transport-efficiency）**：Excel/JSON 导入搬运效率数据、按项目某日查询、历史数据访问。
- **组织架构（organization）**：公司 / 部门（organizations 相关接口），人员画像（DepartmentProfileManager / 工程师画像，供派单精排）。
- **工单截止预警 + 逾期通知**：`dags/notification_dag.py`（Airflow，每小时）：临期 24~25h / 60~120min 两次预警（模板 9），逾期按天升级通知受理人 + 上级（模板 6），无状态无去重文件、整点窗口天然去重。
- **AI 日报总结**：`backend/dags/ai_summarize_dag.py` 调 AI 服务生成日报/周报。

### 3.5 数据模型（约 24 张表，分 7 域）

| 域 | 表 | 来源 |
|---|---|---|
| 身份与权限 | users / roles / permissions / role_permissions / user_project_roles（含 report_to_id 汇报链） | AAS |
| 项目交付 | project / risk / project_daily_report / project_license / realtime_data / history_data / collection_data | DAS |
| 任务工单 | tasks / task_comments / task_comment_read | HelpDesk（ticket 升格） |
| 派单日志 | task_dispatch_log | 智能派单/重派 |
| 咨询对话 | conversations / messages | AI 问询 |
| 模块树 | module_trees / module_tree_nodes / module_tree_edits（审批单） | 派单知识底座 |
| 组织/资源 | companies / departments / resources / resource_folders | 组织架构 / resource_manager |

### 3.6 核心设计要点与工程债

设计要点：单一 ORM 归属（`models/base.py`）、RBAC 逐路由鉴权（`require_permission(perm, project_id)` 支持 admin 直通与 `resource:*` 通配）、任务状态机（`TaskStatus` 枚举 + `ALLOWED_TRANSITIONS`）、双 token（access 30min / refresh 7day）、外部任务源插件化（`TASK_SOURCES_ENABLED`）、通知线程池异步（MQTT + 微信模板消息）、评论区实时 WebSocket。

工程债（详见 `backend/CODEBASE_OVERVIEW.md` §9，部分已改善）：建表双轨（create_all 与 Alembic 并行）、全局 JWT 中间件未启用（靠逐路由 Depends）、shim 层冗余、重复实现（密码哈希/日志）、`setup_logging()` 未被调用、AI 配置新旧双套（AI_* / LLM_*）。

---

## 四、前端 `frontend/`（React H5）

### 4.1 技术栈与命令

Vite 7 + React 19 + TS 5.9（strict）+ TDesign Mobile React 0.23 + React Router v7 + Zustand 5 + ECharts 6 + lucide-react + vitest（jsdom + Testing Library）。

```bash
npm run dev            # localhost:5173，/api 代理（/api/ai/*→8401，其余→8400）
npm run build          # tsc -b + Vite 生产构建（vendor 拆分）
npm run build:test     # base='/t/app/'（配合 nginx /t/* 分发）
npm run build:prod     # base='/p/app/'（配合 nginx /p/* 分发）
npm run test           # Vitest
```

### 4.2 路由架构

```
/login, /no-permission                          # 无守卫
/app  → AuthGuard > MainLayout（底部三 Tab）
  ├─ /app/call            → CallView      （我要摇人）
  ├─ /app/call/history    → HistoryTicketsPage（历史工单列表，独立路由）
  ├─ /app/call/ticket/:id → TicketDetailPage（历史工单详情，全屏）
  ├─ /app/tasks           → TasksView     （系统任务）
  ├─ /app/tasks/:id       → TasksView（直接打开工单详情）
  └─ /app/admin           → AdminView（入口卡片网格）
       └─ AdminLayout（Navbar + ☰ 抽屉菜单）→ 40+ 个管理子页
旧路由（/、/home、/call、/tasks、/admin/*、*）全部 Navigate 重定向到 /app/call。
```

### 4.3 三大视角页面构成

- **CallView（我要摇人）**：上 `ChatPanel`（AI 对话，SSE 流式）+ 下 `HistoryTickets`（我提交的工单，虚拟滚动）；历史工单列表/详情为独立路由页。
- **TasksView（系统任务）**：上 `ChatPanel compact` + 下工单卡片列表（搜索/状态筛选/分页）+ Popup 工单详情（催办/修改/讨论/升级上报/重派）；`TicketDetailPage` 全屏详情（含 DiscussionPanel 评论区、WS 实时）。
- **AdminView（后台管理）**：按角色渲染入口卡片，40+ 管理页。

### 4.4 API 层（`src/config/api.ts` 五服务）

| 服务 | BASE_URL | 说明 |
|------|----------|------|
| AUTH | `${API_ROOT}/auth` | 登录/注册/刷新/用户信息 |
| CALL | `${API_ROOT}/call` | AI 对话/会话/消息/我的任务（业务后端侧） |
| AI | `${API_ROOT}/ai` | 诊断 Agent / LLM / 记忆（AI 服务 @8401） |
| TASKS | `${API_ROOT}/tasks` | 工单 CRUD/评论/附件/状态流转/WS |
| ADMIN | `${API_ROOT}/admin` | 项目/风险/日报/用户/角色/权限/资源/模块树/搬运效率 |

`src/api/client.ts`：通用 fetch 封装（GET 5 分钟缓存、401 自动刷新 pub-sub 防并发、500+ 重试 2 次指数退避、30s 超时、统一 ApiError）；`src/api/ai.ts`：SSE 流式（fetch + ReadableStream，不走 client.ts）；`src/api/ws.ts`：评论实时 WebSocket 封装。

### 4.5 状态管理（两个全局 store，不再新增）

- `stores/auth.ts`：isLoggedIn/username/token/isAdmin/roles，URL token 提取在 RouterProvider 渲染前（防旧路由重定向丢 query）。
- `stores/workbench.ts`：三视角联动中枢——`ticketDraft`（Call→Tasks）、`chatContext`（Tasks→Call）、`selectedTicketId`、`tasksRefreshKey`、`activeTab`；统一入口 `goToTab(tab, payload)`，消费型字段 `consumeXxx()`。
- `stores/dashboardCache.ts`：后台看板数据缓存。

### 4.6 关键共享组件（`src/shared/`）

- `ChatPanel.tsx`：可复用 AI 对话面板（SSE 流式、点赞点踩、语音、上传拍照、转工单二次确认弹窗、对话历史 DB 持久化、流式断连恢复轮询）。
- `MarkdownRenderer.tsx`：GFM 解析（stripCodeFences → preprocessMediaUrls → react-markdown）+ AuthImage Bearer 鉴权图片 + MdErrorBoundary 兜底。
- `AttachmentViewer.tsx`：统一附件预览（图片灯箱 / PDF iframe / Markdown 渲染 / 微信 JS-SDK 原生预览，防 XSS）。
- `DiscussionPanel.tsx`：评论区（气泡、@U老师、附件、WS 实时、已读回执）。
- `TitleEllipsis.tsx`（标题折叠/展开）、`ImageLightbox.tsx`（图片灯箱）、`SafeHtml.tsx`（dompurify）、`RedispatchCandidateList.tsx`（重派候选列表）、`ProjectSelect/UserSelect/UserAvatarMenu/SubscriptionReminder/PullToRefresh/EmojiPicker` 等 31 个组件。
- Hooks：`useTaskCommentsWS`（评论 WS）、`useInertiaScroll`（类 GSAP 惯性滚动）、`usePointerDragToDrop`、`useSubscriptionCheck`、`useHorizontalScroll`。
- 工具：`time.ts`（后端时间解析统一入口：`parseBackendDate/parseBackendDayjs/formatBackendTime`，**禁止直接 `new Date(str)`**）、`deadline.ts`、`authGuard.tsx`（微信 OAuth 跳转 + state 编码回跳）、`wechatJsSdk.ts`、`imageCompress.ts`、`userIdentity.ts`、`ticketFilters.ts`、`projectLifecycle.ts`、`settlement.ts` 等。

### 4.7 工程约定

路径别名 `@/` → `src/`；页面 `export default function` + `React.lazy` 懒加载；`AuthGuard` 仅包裹 `/app`；lint 以 tsc 为准（`no-explicit-any` 为 error）；TDesign 变量已映射到马卡龙设计系统（oklch token + 蒂芙尼蓝主色）；构建 `manualChunks` 拆 echarts/tdesign/react。

---

## 五、AI 服务 `ai/`（FastAPI @8401，挂载 `/api/ai/*`）

### 5.1 服务总览

| 前缀 | 功能 | 说明 |
|------|------|------|
| `/api/ai/qa/*` | 诊断问答 Agent | 统一问答、SSE 流式、工单生成/确认/草稿、附件上传、健康检查 |
| `/api/ai/chat/*` | 纯 LLM 对话 | 非流式/流式 |
| `/api/ai/memory/*` | 会话记忆 | 对话历史、待派单列表、历史工单、清除会话 |
| `/api/ai/task/*` | 任务 Agent | 诊断报告（帮我分析）、@U老师 讨论、摘要、提交方案 |
| `/api/ai/wecom/*` | 企业微信集成 | 项目拉取/分页/更新（Smartsheet） |
| `/api/ai/analysis/*` | 数据分析平台 | 数据分析、快速对话、分析类型、日报/周报生成 |
| `/api/ai/assigner/*` | 派单配置 | `reload` 热更新模块树 + 失效画像缓存 |

统一返回 `{code, message, data}`；SSE 事件：`message_created / first_token / token / status / result / title / memory_written / file_saved / vision_* / error / done`（心跳 `: ping` 保活）。

### 5.2 三大 Agent（`ai/agents/`）

| Agent | 文件 | 职责 |
|---|---|---|
| **AiDiagnosisPlatform**（诊断） | `pipeline.py` / `tool_loop.py` / `search_tool.py` / `ticket_tool.py` / `assigner.py` | 对话→检索→诊断→补信息→生成工单草稿→确认入库 全流水线；工具循环（知识库检索 / 工单）；智能派单精排（候选召回 + LLM 打分 + 倾向人/对接人保底） |
| **AiTaskPlatform**（任务助手） | `pipeline.py` / `product_registry.py` | 系统任务侧 AI：诊断报告、讨论、摘要、提交方案；产品注册表 |
| **AiDataAnalysisPlatform**（数据分析） | `agent.py` / `analyzer.py` / `router.py` / `report_generator.py` / `llm_client.py` | 搬运效率等数据分析 + 日报/周报生成 |

### 5.3 核心组件（`ai/core/`）

| 文件 | 职责 |
|---|---|
| `llm.py` / `embed.py` | LLM 多提供商客户端（DeepSeek 等）、Embedding（bge-small-zh-v1.5） |
| `memory.py` / `conversation_store.py` | 会话记忆（Redis 短期 + MySQL conversations/messages 落库） |
| `retrieval.py` / `reranker.py` / `project_matcher.py` | 知识库 RAG 检索 + 重排 + 项目匹配 |
| `task_adapter.py` | AI ticket ↔ backend Task 适配层（字段映射、时间口径、meta 透传） |
| `log_cache.py` / `chat_snapshot.py` | 日志附件稳定缓存目录（根治重复下载解压）、对话快照 |
| `minio_client.py` / `database.py` | 独立 MinIO / 数据库连接（不依赖 backend） |

### 5.4 知识库与集成

- `ingestion/`：知识摄取（FAQ / 操作手册 / 故障排查），`parsers/kb_markdown.py`、`snapshot_manager.py`（快照）、`registry.py`、`ingest_all.py`。
- `kb/`：向量库封装（Qdrant）。
- `integrations/wecom/`：企业微信 Smartsheet 集成（`smartsheet.py` / `token.py` / `types.py` / `user_map`）——项目从企微表格同步，`ensure_user_project_role_by_name` 按角色名关联项目成员。

---

## 六、自动化测试平台 `automation/`

**数据驱动自动化测试框架：Excel 用例 + Mock 后端 + pytest** —— 新增接口测试只需在 Excel 加一行，无需写代码。

```
automation/
├── src/                       # 框架库：runner（数据驱动执行器）/ clients（ApiClient/MySQL/Redis/Qdrant）
│                              #  assertions / fixtures / logger（控制台+Allure）/ mocks（MockBackend httpx.MockTransport）
│                              #  ai_metrics（LLM judge / retrieval recall 等 AI 评估指标）
├── config/                    # 环境隔离：local/ sit/ uat/ 各含 config.yaml
├── tests/                     # 按业务模块：call / tasks / admin / auth / ai
├── testdata/cases/            # Excel 测试用例（数据驱动核心）
├── references/                # 原始文档库（PRD / 接口文档 / 用例）
├── scripts/ ci/ docker/ docs/ # CLI 工具 / CI 脚本 / 测试容器 / 文档
└── conftest.py                # 跑完自动生成并弹出 Allure 报告
```

另有 `automation/ci_ai_gen/`（AI 生成 CI 用例）。

---

## 七、DAG / 部署 / 脚本

### 7.1 Airflow DAG

| 文件 | 职责 |
|---|---|
| `dags/notification_dag.py` | 工单截止预警 + 逾期通知（每小时；临期 24~25h / 60~120min 两次预警模板 9；逾期按天升级通知受理人+上级模板 6；无状态整点窗口天然去重） |
| `backend/dags/ai_summarize_dag.py` | AI 日报/周报总结 |

### 7.2 部署 `deploy/`

- `deploy/nginx/conf/`：app_gateway 配置，按环境前缀分发——`/t/*`（测试）、`/p/*`（生产），`/api/ai/*` → AI(8401)、其余 `/api/*` → 业务后端(8400)。
- `backend/deploy_redispatch.py`：二次派单感知增强的数据库部署脚本（`_add_col` 幂等，information_schema 检测失效不中断）。

### 7.3 运维脚本 `scripts/`

| 脚本 | 用途 |
|---|---|
| `import_users_from_csv.py` / `delete_duplicate_user_accounts.py` | 用户导入 / 去重 |
| `migrate_task_assigned_to_user_id.py` / `migrate_task_created_by_user_id.py` / `migrate_tz_to_utc.py` | 字段与时区迁移（DB 统一存 naive UTC） |
| `setup_mysql.ps1` / `init_help_manuals.sh` | 环境初始化 / 帮助手册导入 |
| `check.py` / `update_ai_doc.py` | 健康检查 / AI 文档同步 |

---

## 八、文档中心 `docs/`

| 文档 | 内容 |
|---|---|
| `PRODUCT/` | 产品形态（PRODUCT.md）、架构设计蓝图（ARCHITECTURE.md）、搭建部署（SETUP.md）、微信配置（WECHAT.md）、团队分工（TEAM.md）、知识库版产品功能文档、服务号平台手册、兜底双工单方案、转工单统一确认弹窗方案、工单信息补全编辑方案、微信附件预览下载方案、需求流转与版本管理设计方案、usp 诊断知识库、工单确认后对话气泡概览方案 |
| `project_architecture.md` | 测试视角的架构说明 |
| `PRD.md` / `business_rules.md` / `prompt_library.md` / `troubleshooting.md` | 需求 / 业务规则 / 提示词库 / 故障排查 |
| `AiDiagnosisPlatform_agent_flow.md` / `ORS算法模块图.md` / `USP服务器日志自动拉取分析方案.md` | AI Agent 流程 / 算法模块 / USP 日志方案 |
| `实时评论WebSocket设计方案.md` / `讨论区消息转发到微信方案.md` | WS / 微信转发设计 |
| `AI代码修改边界Skill.md` / `agents/` | AI 协作规范 / Agent 设计 |
| `CODEBASE_OVERVIEW.md` | 本文档 |

---

## 九、请求流（一次 AI 摇人闭环）

```
微信菜单 → /p/app/call（nginx → frontend）
  → ChatPanel SSE 提问 /api/ai/qa/ask/stream（nginx /p/api/ai/* → AI 8401）
     → AiDiagnosisPlatform.pipeline：检索知识库(ingestion+kb) → LLM 诊断 → 工具循环
     → 生成工单草稿 → 前端「转工单」二次确认弹窗（/api/ai/qa/ticket/confirm）
     → 落库 tasks（AI 侧 task_adapter 写 backend MySQL）
     → 智能派单 assigner（模块树召回 + 画像精排 → task_dispatch_log）
     → 微信模板消息通知受理人 → 前端系统任务处理（讨论 WS 实时、上报升级）
     → 关闭/交付 → 经验回流知识库
```

---

## 十、一句话概括

这是一个**架构设计完整、底座（RBAC + 任务工单 + 微信外壳 + 交付管理 + 禅道集成）已落地、AI 能力（多提供商问答 + RAG 知识库 + 诊断/任务/数据分析三 Agent + 智能派单）持续演进**的多模块单体仓库：业务后端（FastAPI）+ AI 独立服务（FastAPI）+ React H5 前端三服务协作，配套数据驱动自动化测试平台、Airflow 定时任务与 nginx 多环境部署。
