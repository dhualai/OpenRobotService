# 后端改造计划（MIGRATION）

> 本文定义 OpenRobotService 后端从**遗留迁移模块**（aas / das / fqa / wechat）向 [TEAM.md](./TEAM.md)「目录结构」目标形态的改造路线。
> 目标架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)，团队分工见 [TEAM.md](./TEAM.md)。

---

## 一、现状 vs 目标：差距总览

目标骨架目录**均已建好但全空**（`models/`、`schemas/`、`kb/`、`ai/agents/`、`modules/{call,tasks,admin}/`），
真实代码全在遗留迁移模块中：`aas`（承 AAS）、`das`（承 DAS）、`fqa`（承 HelpDesk）、`wechat`。
改造 = 把遗留模块**按垂直视角拆解重组**进目标结构，同时**统一底座数据模型**。

| 层 | 目标 | 当前实际 | 差距 |
|---|---|---|---|
| 底座·数据模型 `app/models/` | 统一 ORM 模型 | **空**；模型散落 `core/database.py`、`das/models`、`fqa/*/models`、`core/models.py`，`Project` 被定义两次且表名冲突（`projects` vs `project`） | 🔴 最大 |
| 底座·契约 `app/schemas/` | 统一 Pydantic 契约 | **空**；schemas 散在各模块 | 🔴 |
| 底座·core `app/core/` | 配置/DB/安全/DI/USP 接缝 | 已有 config/database/security/middleware；`database.py` 34KB 混入建表+ORM+查询 | 🟡 需拆分 |
| 微信外壳 `app/wechat/` | 顶层微信模块 | `app/modules/wechat/`（活跃）+ `app/wechat/`（**30 文件死代码副本**） | 🟡 去重+上移 |
| 知识库 `app/kb/` | 五层知识 + RAG | **空** | 🔴 全新建 |
| AI `app/ai/agents/` | 基础层 + 三 Agent | **空** | 🔴 全新建 |
| 垂直模块 `modules/{call,tasks,admin}/` | 我要摇人/系统任务/后台管理 | **三个空占位**；逻辑在 aas/das/fqa | 🔴 重组 |
| 共享业务 `app/services/` | 跨模块业务 | 已有 hmac/logging/user_service | 🟢 基本就位 |
| USP 接缝 `/api/integration/usp/*` | 机器对机器入站接口 | 散在 `das/api/data.py`（access/insert/upload） | 🟡 归拢+独立鉴权 |

---

## 二、遗留端点 → 目标模块 映射表

约 110 个端点的归属总账：

| 目标位置 | 承接来源 | 说明 |
|---|---|---|
| **core + models + services（底座）** | `aas/*`（auth/users/roles/projects/permissions）全部 | AAS 不是功能模块，是认证/RBAC 底座；User/Role/Project/Permission 收敛为唯一 ORM 模型 |
| **modules/call（我要摇人）** | `fqa` 的 `ask`/`ask/stream`、`conversations`、`ticket`（我的工单视角） | 报障提单 + AI 咨询 + 我的工单跟进。**请求方视角** |
| **modules/tasks（系统任务）** | `fqa/ticket` 全部（工单/评论/状态机/派单/AI 分配/催办） | 统一任务收件箱。**处理方视角**，与 call 共享同一 `tasks` 表 |
| **modules/admin（后台管理）** | `das` 的 projects/risks/daily-reports/export/licenses + `aas` 项目/成员管理 + 机器人故障统计 | 跨项目看板、项目/风险/日报/授权管理 |
| **wechat（外壳）** | `modules/wechat/*` 全部 + `das/notify` | 菜单/消息/标签/OAuth/模板通知；删除 `app/wechat/` 死副本 |
| **kb（知识库）** | 🆕 全新 | RAG 检索服务，`kb_documents` + Qdrant |
| **ai（AI 基础层+三 Agent）** | `fqa/qa/services/model_service`（DeepSeek 接入）承接 | 统一 LLM 接口 + RAG + SSE + 三套 prompt |
| **USP 接缝** `/api/integration/usp/*` | `das/data`（access/history/insert/upload） | 独立 API Key 鉴权，落 `robot_faults`/`robot_task_stats` |
| **共享存储服务** `app/services/storage/` | `fqa/resource_manager/*`（约 30 端点，含 MinIO/OSS） | **决策 D1：降为共享存储服务**——工单附件、知识库素材的底层能力，不作独立菜单模块 |
| **基础设施 worker** `app/worker/` | `fqa/tasks`（Celery 异步队列） | **决策 D3：改名**，与业务「系统任务」`modules/tasks` 区分 |

---

## 三、分阶段路线图

遵循 TEAM.md「底座先行、滚动递进」：契约先冻结 → 统一底座数据模型 → 三垂直模块并行搬迁 → 补 kb/ai → 收 USP 接缝。

### 阶段 0 — 冻结契约 & 清障（全员共建）
> TEAM.md 风险 #1：接口契约必须先冻结，这是垂直并行的前提。

- [ ] 产出《数据字典》与《API 契约》（产品经理 owner）：定稿 ARCHITECTURE.md 第三节全部表结构与字段。
- [x] 删除死代码 `app/wechat/`（已确认无 `from app.wechat` 外部引用；该副本内部反向依赖 `app.modules.wechat`，安全删除）。
- [x] 统一 `Base`：`das/models/models.py` 改为 `from app.core.database import Base`，废除其独立 `declarative_base()`。现全项目单一 metadata（19 张表，含 core+das+fqa），已验证两个 `Project` 类（表 `projects`/`project`）共存无 registry 冲突。

> **阶段 0 工程项已完成**（2026-07-10）：应用导入正常，163 条路由完好，四大模块路由组全部保留。
> 前置事实（已核实）：core / das / fqa 三处 `DATABASE_URL` 均指向同一物理库 `helpdesk`（127.0.0.1:3306），故统一 Base 无跨库风险。das 模型无任何 `relationship()`/`ForeignKey`，合并 metadata 安全。
> 剩余《数据字典/API 契约》为产品经理文档交付物；本轮已产出的路由清单可作为 API 契约盘点输入。

### 阶段 1 — 底座数据模型统一（🔴 最高优先级，后端工程师 owner）
> 不统一模型，后面所有垂直模块都在流沙上盖楼。

> **阶段 1 Wave 1 已完成**（2026-07-10）：ORM 收敛进 `app/models/`（19 表单一 metadata）、底座契约收敛进 `app/schemas/`、Alembic 工具链就位（`alembic.ini`/`env.py`/`0001_baseline`，现有库已 `stamp`）。全程用再导出 shim，24+ 引用文件零改动；163 条路由完好、登录冒烟全通。`alembic check` 仅剩 84 个 `modify_comment`（遗留库列注释，无结构性差异）。**破坏性部分（双 Project 合并、tickets→tasks、删影子类）拆为 Wave 2**，见第七节。

- [ ] `app/models/` 建唯一 ORM：`user` / `project` / `role` / `permission` / `task`(承 ticket) / `task_comment` / `risk` / `daily_report` / `license` / `robot_fault` / `robot_task_stat` / `conversation` / `message` / `kb_document`。
- [ ] **消解 `Project` 双定义**：合并 `core.database.Project`(表 `projects`) 与 `das.Project`(表 `project`) 为一张表，写 Alembic 数据迁移。
- [ ] **`ticket` → `task` 语义升格**：`tickets` 表重构为 `tasks`（加 `type` 字段：problem/bug/feature/support）。
- [ ] 删除 `core/models.py` 的非 ORM 影子类，统一走 ORM + Pydantic。
- [ ] `app/schemas/` 集中 Pydantic 契约。

### 阶段 2 — 拆分 `core/database.py`（后端工程师）
- [ ] 34KB 巨文件拆为：`core/db.py`(引擎/session/get_db) + `models/*`(ORM) + `services/*`(查询逻辑)。
- [ ] `DatabaseManager` 的业务查询下沉到对应 service。

### 阶段 3 — 垂直模块重组（三人并行，各 owner 一个切片）
- [ ] **modules/admin**：搬 das 的 projects/risks/daily-reports/export + aas 项目管理 → `/api/admin/*`。
- [ ] **modules/tasks**：搬 fqa/ticket → `/api/tasks/*`，落地状态机 `ALLOWED_TRANSITIONS`。
- [ ] **modules/call**：搬 fqa/qa + conversations + 我的工单视角 → `/api/call/*`。
- [ ] **wechat**：`modules/wechat` 上移到 `app/wechat/`，合入 das/notify。

### 阶段 4 — 新建 kb + ai（AI 工程师）
- [ ] `app/kb/`：`kb_documents` 模型 + Qdrant 接入 + 检索 API（承接 fqa `model_service` 的 LLM 封装）。
- [ ] `app/ai/`：统一 LLM 接口 + RAG + SSE + 三 Agent（提单/任务/管理 prompt）。
- [ ] 打通「工单关闭 → AI 总结 → 人工审核 → 入库」知识生产闭环。

### 阶段 5 — USP 接缝 + 收尾
- [ ] das/data 的 access/insert/upload 归拢为 `/api/integration/usp/*`，独立 API Key 鉴权。
- [ ] 落 `robot_faults` / `robot_task_stats`，供 admin 看板。
- [ ] 删除已清空的 `modules/{aas,das,fqa}`，更新 `app/__init__.py` 路由注册。

---

## 四、决策记录

| # | 决策点 | 结论 |
|---|---|---|
| **D1** | `resource_manager`（资源/文件管理，约 30 端点）在目标架构无位置 | ✅ **降为共享存储服务** `app/services/storage/`，服务于工单附件与知识库素材，不作独立菜单模块 |
| **D2** | 改造激进度：一次性大重构 vs Strangler 渐进 | 建议**渐进**：保留旧路由 + 新增目标路由，逐模块迁移后再删旧，降低前端停摆风险 |
| **D3** | fqa 异步队列（Celery）与「系统任务」重名 | ✅ 前者改名 `app/worker/`，`modules/tasks` 专指业务工单 |
| **D4** | 旧 API 路径兼容（`/api/AAS|DAS|fqa/*`） | 保留兼容期，新路径 `/api/{auth,call,tasks,admin,wechat,integration}/*`，前端迁移完成后下线 |

---

## 五、关键风险

1. **模型合并涉及数据迁移**（阶段 1）：`projects`/`project` 双表合并、`tickets`→`tasks` 重构，是整个改造风险最集中处，须先在 Alembic 中演练。
2. **接口契约必须先冻结**（TEAM.md 风险 #1）：底座数据模型/API 规范/前端组件契约先定，否则三垂直模块并行会互相打架。
3. **前端一致性与旧路径依赖**：前端已对接 `/api/AAS|DAS|fqa/*`，改造期须维持兼容路由（见 D4）。
4. **集成需专人兜底**（TEAM.md 风险 #4）：5 模块端到端跑通建议后端工程师或团队共同职责。

---

## 六、目标目录结构（参照 TEAM.md）

```
backend/app/
├── core/ models/ schemas/       # 底座：配置/统一数据模型/契约/认证 RBAC
├── wechat/                      # 微信外壳（去重后上移）
├── kb/  ai/agents/              # 知识库 + AI 基础层 + 三 Agent
├── modules/{call,tasks,admin}/  # 三垂直功能模块
├── services/                    # 跨模块共享业务（含 storage/ 存储服务）
└── worker/                      # Celery 异步任务基础设施（原 fqa/tasks）
```

---

## 七、剩余改造详细说明（Wave 1 之后）

> Wave 1（阶段 1 的零风险部分）已完成。本节把剩余全部工作按依赖链展开，
> 每块给出：目标 → 步骤 → 触及文件 → 所需 Alembic 迁移 → 风险 → 验证。

### 依赖链总览

```
Wave 2（底座破坏性迁移）──┐
                          ├─→ 阶段 3（三垂直模块并行）─→ 阶段 5（USP + 收尾）
阶段 2（拆 database.py）──┘         ↑
阶段 4（kb + ai，可与阶段 3 并行）──┘
```

关键前置：**Wave 2 与阶段 2 必须先于阶段 3**——垂直模块搬迁依赖「干净的模型 + 拆分后的 service 层」。阶段 4（kb/ai）不依赖 Wave 2，可与阶段 3 并行。

---

### Wave 2 — 底座破坏性迁移（🔴 风险最集中）

三件事均涉及**真实数据迁移**，从 `0001_baseline` 派生 Alembic 修订、逐个评审。

#### 2.1 双 `Project` 合并（最难）

现状症结：
- `projects`（String 主键 `id`＝项目 code、`code`、`name`）＝ RBAC 锚点；`user_project_roles.project_id` 外键指向 `projects.id`。
- `project`（Integer 自增 `id`、`project_code` 唯一 + 20 个交付业务字段）＝ 交付档案；DAS 全程用字符串 `project_code` 关联，从不 join 该 Integer 主键。

合并策略（推荐：以业务键 `code` 为统一主键）：
1. 建统一表 `project`（保留交付 20 字段），**主键改为 `code`（String）**，废弃 Integer 自增。
2. 数据迁移三步对账：交付表 `project` 行按 `project_code` 灌入；`projects`（RBAC）有、交付无的项目（如默认 `TEST`）补「瘦」行；交付有、RBAC 无的按业务定夺。
3. `user_project_roles.project_id` 外键从 `projects.id` **重指统一表 `code`**（值本就相等，数据无需变换）。
4. 删旧 `projects` 表；`identity.Project` 与 `delivery.ProjectDelivery` 合并为单一 `Project` 类。

触及：`app/models/{identity,delivery}.py`、`das/services/project_service.py`、`aas/api/projects.py`、`aas/services/*`、新 Alembic 修订（含 `op.execute` 数据搬运）。
风险：外键重指 + 主键类型变更是全项目最危险处；**先在测试库演练 `upgrade`→`downgrade` 往返**，并备份 `helpdesk` 库。
验证：迁移往返成功；RBAC 分配、DAS 项目列表、成员查询照常。

#### 2.2 `tickets` → `tasks` 语义升格

做什么：`tickets`/`ticket_comments` 重命名为 `tasks`/`task_comments`；模型 `Ticket`→`Task`；`ticket_type` 语义确立为 problem/bug/feature/support。落地 ARCHITECTURE.md「任务是统一抽象、工单是其类型」。
触及：`app/models/ticket.py`→`task.py`、`fqa/ticket/*`（服务/schema/api，17 端点）、Alembic `op.rename_table`。
风险：中（重命名波及面）；用 shim 过渡（`ticket.py` 再导出 `Task as Ticket`）平滑。
验证：工单 17 端点冒烟；状态机 `ALLOWED_TRANSITIONS` 仍生效。

#### 2.3 删除 `core/models.py` 影子类

做什么：删非 ORM 的 `User/Token/Role/Project/RoleAssignment` 手写 DTO，统一走 ORM + `app/schemas` Pydantic。
难点：`DatabaseManager.get_user()` 按位置实例化 `User(...)` 并动态挂 `roles/projectPermissions/external_credentials`，`auth_service`、`user_service` 依赖此。删除前先把消费点改读 Pydantic `UserInDB`/ORM。
建议：与阶段 2 的 `DatabaseManager` 拆分**同批做**（耦合同段查询逻辑）。

---

### 阶段 2 — 拆分 `core/database.py`（34KB 巨文件）

现状：一个文件混了 引擎/session/`get_db` + `DatabaseManager`（20+ 业务查询）+ `get_user_with_roles`（复杂权限聚合）。ORM 已在 Wave 1 抽走，剩查询逻辑。

拆法：
- `core/db.py`：引擎、`SessionLocal`、`async_engine`、`get_db`/`get_async_db`（纯基础设施）。
- `DatabaseManager` 方法按域下沉：用户/角色 → `services/user_service.py`、权限聚合 → `services/permission_service.py`、项目 → admin 模块 service。
- `core/database.py` 降为薄再导出 shim，过渡期保留，调用方改指后删除。

触及：`core/database.py` 的 10 个 `db_manager` 使用点、`aas/services/*`。
风险：中；`get_user_with_roles` 权限聚合复杂，拆时保行为一致，配单测兜底。
验证：登录/取用户/权限校验行为不变（对比 `/auth/me` 输出）。

---

### 阶段 3 — 三垂直模块重组（三人并行，各 owner 一片）

Strangler 渐进：**新增目标路由 + 保留旧路由兼容**，前端切换后再删旧（决策 D2/D4）。

| 新模块 | 承接来源 | 新路由前缀 | 视角 |
|---|---|---|---|
| `modules/admin` | DAS projects/risks/daily-reports/export/licenses + AAS 项目管理 + 机器人故障统计 | `/api/admin/*` | 跨项目看板 |
| `modules/tasks` | fqa/ticket 全部（工单/评论/状态机/派单/AI 分配/催办） | `/api/tasks/*` | 处理方 |
| `modules/call` | fqa ask/ask-stream + conversations + 「我的工单」视角 | `/api/call/*` | 请求方 |
| `wechat`（上移） | `modules/wechat/*` 全部 + das/notify | `/api/wechat/*` | 外壳 |

搬迁套路：api → 新前缀；service 依赖改指阶段 2 拆出的底座 service；schema 视是否跨模块决定迁 `app/schemas/` 或留模块内；旧路由在 `app/__init__.py` 保留兼容注册。
风险：前端已对接 `/api/AAS|DAS|fqa/*`，兼容路由须维持整个阶段 3（D4）。
验证：新旧路由并存期，同功能新旧路径返回一致。

---

### 阶段 4 — 新建 kb + ai（AI 工程师，可与阶段 3 并行）

`app/kb/`（知识库）：
- 新增 `kb_documents` 模型（`layer` 行业/公司/团队/项目/个人、`source` static/summary/case、`status` 审核态）→ 加进 `app/models/` + 一条 Alembic 建表。
- 接入 Qdrant + Embedding，提供「问题 → 相关知识/案例」检索 API。
- 打通闭环：工单关闭 → AI 总结 → 人工审核 → 入库。

`app/ai/`（AI 基础层 + 三 Agent）：
- 统一 LLM 接口（封装 DeepSeek，承接 fqa `qa/services/model_service`）、RAG 调 kb、SSE 流式、上下文管理。
- 三 Agent＝共享基础层 + 各自 prompt/工具：提单（需求）、任务（供给）、管理（管理）。

依赖：kb 先于 ai（Agent 调 kb 检索）。不依赖 Wave 2。

---

### 阶段 5 — USP 接缝 + 收尾

- USP 接缝：`das/data` 的 access/history/insert/upload 归拢为 `/api/integration/usp/*`，改用**独立 API Key 鉴权**（与用户 JWT 分离），落 `robot_faults`/`robot_task_stats` 两张新表供 admin 看板。
- 收尾：删已清空的 `modules/{aas,das,fqa}`、下线兼容旧路由、路由统一到 `/api/{auth,call,tasks,admin,wechat,integration}/*`、移除 `create_all` 改纯 Alembic 建表、`fqa/tasks`（Celery）改名 `app/worker/`（D3）。

---

### 建议执行顺序与工作量粗估

| 顺序 | 内容 | 相对工作量 | 阻塞谁 |
|---|---|---|---|
| 1 | 阶段 2 拆 `database.py` + Wave 2.3 删影子类 | 中 | 阶段 3 |
| 2 | Wave 2.1 Project 合并 | 高（数据迁移） | admin 模块 |
| 3 | Wave 2.2 tickets→tasks | 中 | tasks 模块 |
| 4 | 阶段 3 三模块并行 | 高（可并行） | 阶段 5 |
| 5 | 阶段 4 kb + ai | 高（可与 3 并行） | — |
| 6 | 阶段 5 USP + 收尾 | 中 | 完成 |

建议：下一步先做 **阶段 2 拆 `database.py` + Wave 2.3 删影子类**（耦合、且为阶段 3 前置、无数据迁移、风险可控）；最危险的 **Wave 2.1 Project 合并**放在有库备份、能在测试库演练往返之后单独做。
