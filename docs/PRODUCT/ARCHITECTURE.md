# 架构设计

> 本文定义 OpenRobotService 的技术实现。产品形态见 [PRODUCT.md](./PRODUCT.md)。

---

## 一、总体架构

```
                ┌─────────────────────────────────────────┐
                │            微信服务号（公众号）            │
                │    底部三菜单 · 消息对话 · 模板消息通知     │
                └───────────────────┬─────────────────────┘
                                    │ HTTPS
                ┌───────────────────▼─────────────────────┐
                │            前端 H5（Vue3 + Vant）         │
                │  我要摇人 · 系统任务 · 后台管理（角色化）   │
                └───────────────────┬─────────────────────┘
                                    │ REST / SSE(流式)
                ┌───────────────────▼─────────────────────┐
                │              后端（FastAPI 单体）         │
                │  ┌────────┐┌────────┐┌────────┐┌───────┐ │
                │  │ wechat ││功能模块 ││  ai    ││ kb    │ │
                │  │        ││(三视角) ││(三Agent)││知识库 │ │
                │  └────────┘└────────┘└────────┘└───────┘ │
                │  ┌────────┐┌────────┐┌────────┐┌───────┐ │
                │  │  api   ││services││ models ││ core  │ │
                │  └────────┘└────────┘└────────┘└───────┘ │
                └──┬──────────┬──────────┬──────────┬──────┘
                   │          │          │          │
            ┌──────▼───┐ ┌────▼────┐┌────▼────┐┌────▼─────────┐
            │ MySQL 8  │ │ Qdrant  ││  Redis  ││ 商用大模型 API │
            │ 业务数据  │ │ 向量库   ││缓存/队列 ││(DeepSeek 等) │
            └──────────┘ └─────────┘└─────────┘└──────────────┘
                                    ▲
                                    │ 入站数据接口（鉴权）
                          ┌─────────┴──────────┐
                          │   USP 调度平台       │
                          │ 故障数据 / 任务统计  │
                          └────────────────────┘
```

> 说明：后端是**单一 FastAPI 应用**（由内部 projectplatform 的多微服务收敛而来），但依赖一组专业基础设施（向量库、Redis），以 Docker Compose 编排。**效果优先于部署轻量**——官方实例是门面，知识库与检索采用专业方案。

---

## 二、五大技术模块

| 模块 | 职责 | 来源 |
|------|------|------|
| **① 微信服务号** | 菜单、鉴权(OAuth+JWT)、消息对话、模板消息通知 | 移植内部 WeChat 服务 |
| **② 三大功能模块** | 我要摇人 / 系统任务 / 后台管理，各含 H5 页面 + 后端数据与逻辑 | 重构 HelpDesk + DAS |
| **③ 知识库** | 五层知识的摄取、检索(RAG)、动态总结、案例召回 | 新建，素材来自 HelpDesk docs |
| **④ AI 算法** | 三视角各一 Agent，全流程引导 | 新建，承 HelpDesk 的 DeepSeek 接入 |
| **⑤ 核心层 core** | 配置、数据库、安全、依赖注入、USP 接缝 | 新建 |

### ① 微信服务号模块（`app/wechat/`）

- **菜单**：创建/更新底部三菜单（统一菜单，深链 H5）。
- **鉴权**：微信网页 OAuth 静默授权换 openid → 查/建用户 → 签发 JWT。
- **对话**：消息回调（XML 收发、加解密、签名校验），承接"在线咨询"的微信侧入口。
- **通知**：模板消息（派单、新讨论、上报通知相关人）。
- 无微信参数时降级（通知打印日志），便于本地联调。

### ② 三大功能模块（`app/api/` + `app/services/`）

对应三个菜单/三种视角，共享同一套底层数据模型，按视角组织接口与逻辑：

- **我要摇人**：报障提单、AI 咨询、我的工单跟进。
- **系统任务**：统一任务收件箱（工单/bug/需求等多类型），接单、处理、转派、上报。
- **后台管理**：跨项目看板、项目/风险/日报/授权管理、机器人故障与任务统计。

### ③ 知识库模块（`app/kb/`）——核心护城河

做成**统一 RAG 检索服务**，三个 Agent 都调它，不各自管理知识。

**五层知识**：

| 层 | 内容 |
|----|------|
| 行业知识 | 工业移动机器人通用知识、故障原理 |
| 公司知识 | 本公司产品手册、规范、流程 |
| 团队知识 | 团队内部经验、约定 |
| 项目知识 | 特定项目的配置、历史、现场情况 |
| 个人知识 | 个人积累的处理经验 |

**三类来源**：

1. **静态文档**：用户使用手册、FAQ 手册等结构化文档 → 解析、切片、向量化入库。
2. **动态总结**：从历史工单/任务自动提炼的知识 → 经"工单关闭 → AI 总结 → 人工审核 → 入库"流水线沉淀。
3. **相关案例**：近期直接相关的工单/任务作为参考案例，实时召回。

**知识生产闭环**：任务/工单的最终（人工校验过的）处理结果回写知识库，成为新案例与新知识，使产品越用越聪明。

**技术**：Qdrant 向量库 + Embedding 模型；检索 API 对内提供"给定问题 → 返回相关知识/案例"。

### ④ AI 算法模块（`app/ai/`）

三视角各一个 Agent，与三菜单一一对应，全流程深度参与：

| Agent | 视角 | 作用 |
|-------|------|------|
| **提单 Agent** | 需求 | AI 预先自动回答提单问题、引导用户初步诊断与完善信息，解决不了再打包转工单 |
| **任务 Agent** | 供给 | AI 生成部分参考答案，人工编辑补充、校验后提交 |
| **管理 Agent** | 管理 | AI 分析数据、风险提示、关注重点、优化建议 |

**共享 AI 基础层**（避免三套重复实现）：

- **统一 LLM 接口**：封装商用大模型 API，底层可切换厂商（默认 DeepSeek）。
- **RAG 接入**：调知识库检索服务。
- **对话上下文管理 / 流式输出(SSE) / 成本与限流控制**。

三个 Agent 仅是其上的三套 prompt + 工具配置。

**实现方式**：在线调用商用大模型 API + 自有知识库（RAG），不本地部署模型。

---

## 三、数据模型

### 用户与权限（承 BackgroundService/AAS）

- **users** — `openid`, `nickname`, `phone`, `password_hash`, `status`
- **projects** — `code`, `name`, `customer_name`, `status`, 交付信息
- **user_project_roles** — 用户在某项目中的角色，含 `report_to_id`（汇报链，支撑"上报领导"）
- **roles** / **permissions** / **role_permissions** — RBAC，基于 `resource_type` + `action` 的细粒度权限

### 任务（统一模型，承 HelpDesk ticket）

"任务"是上层抽象，工单/bug/需求是其类型：

- **tasks** — `title`, `description`, `type`(problem/bug/feature/support…), `status`, `priority`, `creator_id`, `assignee_id`, `project_id`, `due_at`
- **task_comments** — `task_id`, `author_id`, `content`, `comment_type`(普通/上报/系统)
- **task_assignments** — 派单/转派历史 `from_user_id`, `to_user_id`, `note`

### 项目交付（承 DataAccessService/DAS）

- **risks** — `risk_code`, `project_code`, `category`, `level`, `status`, `responsible_person`
- **daily_reports** — `project_code`, `report_date`, `content`, `reporter`
- **licenses** — `project_code`, `license_code`, `apply_time`, `expire_time`

### 机器人数据（轻 C，由 USP 上传）

- **robot_faults** — 机器人故障/异常：`project_code`, `robot_id`, `fault_code`, `level`, `occurred_at`, `detail`
- **robot_task_stats** — 每日运行任务统计：`project_code`, `robot_id`, `date`, `task_count`, `success_rate`, …

### 咨询与对话

- **conversations** — AI 咨询会话：`user_id`, `scene`, `status`
- **messages** — `conversation_id`, `role`(user/assistant), `content`

### 知识库

- **kb_documents** — 知识条目：`layer`(行业/公司/团队/项目/个人), `source`(static/summary/case), `title`, `content`, `status`(审核态)
- 向量索引存于 Qdrant，与 `kb_documents` 关联。

---

## 四、任务状态机

```
  ┌────────┐  派单   ┌────────┐  开始处理 ┌────────┐
  │待派单  ├────────>│已派单  ├──────────>│处理中  │
  │pending │         │assigned│           │handling│
  └────────┘         └────────┘           └───┬────┘
                                              │
              ┌───────────────┬───────────────┼───────────────┐
              ▼               ▼               ▼               ▼
        ┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
        │待讨论   │     │已上报   │     │已解决   │     │已关闭   │
        │discussing│    │escalated│     │resolved │     │closed   │
        └─────────┘     └─────────┘     └────┬────┘     └─────────┘
                                             │ 重开
                                             └──────> handling
```

合法状态流转在 `services/task_service.py` 的 `ALLOWED_TRANSITIONS` 中定义并强制校验。工单关闭后触发知识生产闭环（AI 总结 → 人工审核 → 入知识库）。

---

## 五、认证流程

1. 用户在微信内打开 H5 → 后端 `/api/wechat/oauth` 发起网页授权。
2. 微信回调带 `code` → 后端用 code 换 `openid`。
3. 按 openid 查/建用户 → 签发 JWT 返回前端。
4. 前端后续请求携带 `Authorization: Bearer <jwt>`；接口按 RBAC 校验。

开发环境可用 `/api/auth/dev-login` 直接以指定角色登录，无微信环境也能联调。

---

## 六、USP 调度平台接缝

OpenRobotService 与 USP 调度平台的边界 = **一个面向 USP 的入站数据接口**（机器对机器，独立鉴权）：

- USP **主动推送/导出**两类数据进来：①机器人故障/异常 ②每日运行任务统计。
- 接口：`POST /api/integration/usp/faults`、`POST /api/integration/usp/task-stats`，用独立的 API Key / 服务账号鉴权，与用户 JWT 分离。
- 平台侧负责落库（`robot_faults` / `robot_task_stats`）、关联项目与工单、供"后台管理"展示。
- 本平台**不主动拉取** USP 的实时遥测，保持边界清晰。

---

## 七、通知机制

任务关键动作触发 `wechat/notify.py` 发送模板消息：

- 派单 → 通知被指派人
- 新讨论 → 通知任务参与者
- 上报 → 通知上级领导（按 `report_to_id` 汇报链）

无微信参数时降级为日志输出。

---

## 八、部署形态

以 **Docker Compose** 编排（与内部既有实践一致）：

| 组件 | 用途 |
|------|------|
| FastAPI 后端 | 业务 + AI + 知识库 |
| MySQL 8 | 业务数据 |
| Qdrant | 知识库向量检索 |
| Redis | 缓存、AI 队列、异步任务 |
| Nginx | 反向代理、HTTPS、托管前端 `dist/` |

- 绝大多数使用者直接用官方「摇人吧」，无需部署。
- 自托管开发者用 Compose 一键起全套。
- 后端生产用 `gunicorn -k uvicorn.workers.UvicornWorker` 多进程。
