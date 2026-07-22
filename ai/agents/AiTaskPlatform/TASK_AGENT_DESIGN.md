# AiTaskPlatform — 任务 Agent 设计文档

> 版本：2.2 | 日期：2026-07-21（Day 2 收尾）
>
> **本文件是 AiTaskPlatform 的权威设计文档**，供开发时参考和每次新对话恢复上下文。
>
> **当前状态**：v2.2 — v2.0 架构重构全部完成。Chat 感知用户全量工单，Diagnosis 后台自动服务，附件 ZIP/文件夹解，task_comments 存储诊断。知识闭环 Layer 1 读+写就绪，待联调验证。

---

## 更新日志

| 日期 | 版本 | 变更摘要 |
|------|:---:|------|
| 2026-07-21 | 2.2 | Day 2 收尾：附件 ZIP/文件夹解+图片识别；Chat API v2.0(username+工单感知)；Prompt 工单感知规则升级 |
| 2026-07-21 | 2.1 | 停晚：tickets→tasks 迁移已完成（`task_adapter.py`）；诊断结果改用 `task_comments` |
| 2026-07-21 | 2.0 | **架构重构**：Chat + Diagnosis 分离；Diagnosis 独立服务+自动触发 |
| 2026-07-21 | 1.2 | Day 2 上午：能力分层路线图（Layer 0-3）+ 迭代时间线 |
| 2026-07-20 | 1.1 | Day 1 下午：全节点埋点（9 个追踪节点）+ CLI 交互工具 + PRD 合规 |
| 2026-07-20 | 1.0 | Day 1 上午：初始交付——7 端点 API + ChatPanel + SolutionCard + tickets 表直读 |

---

## 目录

1. [v2.0 架构总览](#1-v20-架构总览)
2. [职责边界](#2-职责边界)
3. [Chat 自由问答（新）](#3-chat-自由问答新)
4. [Diagnosis 诊断服务（新）](#4-diagnosis-诊断服务新)
5. [诊断状态追踪](#5-诊断状态追踪)
6. [API 契约](#6-api-契约)
7. [数据流](#7-数据流)
8. [前端对接](#8-前端对接)
9. [tickets → tasks 迁移](#9-tickets--tasks-迁移)
10. [目录结构](#10-目录结构)
11. [实现状态](#11-实现状态)
12. [本周开发任务](#12-本周开发任务)
13. [能力分层路线图](#13-能力分层路线图)

---

## 1. v2.0 架构总览

### 架构变化

```
v1.x（旧）:
  ChatPanel
    ├── 无 taskId → chat/stream（自由问答）
    └── 有 taskId → analyze/stream → SolutionCard（聊天内渲染）

v2.0（新）:
  ChatPanel（纯聊天）                      Diagnosis 服务（独立）
    ├── 感知用户所有已派单工单                 ├── 新派单自动触发
    ├── 感知各工单的诊断信息                   ├── 三路分析 → 写入 task_comments（AI任务助手评论）（AI任务助手评论）
    ├── 判断"哪单最急""哪个最难"              ├── 写入 task_comments（AI任务助手评论）
    ├── 无工单也能工程师视角问答               └── 工单详情页静态展示
    └── 不需要 taskId prop
```

### 系统任务页面分工

```
系统任务页面
├── ChatPanel（顶部 AI 助手）
│    └── 任务 Agent chat 接口
│         ├── 调后端 GET /api/tasks/?assigned_to=<username> 获取用户工单
│         ├── 感知所有工单 + 诊断信息
│         ├── 自由技术问答（无工单也能用）
│         └── 不传 taskId（与 v1.x 的关键区别）
│
├── 工单卡片列表（全部/待处理/已完成）
│    └── GET /api/tasks/?assigned_to=<username>&status=...（业务后端 8400）
│         └── 后端按 assigned_to 过滤，显示"分配给我"的工单
│
└── 工单详情 Popup
     └── AI 诊断结果（静态展示，非聊天窗口）
          └── 从 task_comments（AI任务助手评论） 读取，诊断服务预先生成
```

---

## 2. 职责边界

### 提单 Agent（已完成）→ 任务 Agent 的数据流

```
提单 Agent (AiDiagnosisPlatform)
  │
  └── submit() → tickets 表
        └── diagnosis JSON: {problem_summary, hypotheses, ruled_out, collected_info}

─────────────── tickets/tasks 表 ────────────────

任务 Agent (AiTaskPlatform)
  ├── Chat: 读 task_comments（AI任务助手评论），感知用户全量工单
  └── Diagnosis: 新派单自动分析 → 写 task_comments（created_by=AI任务助手）
```

### 铁律（不变）

- ❌ 重新做 5 路 KB 全文检索（提单 Agent 已经做过）
- ❌ 重新推断 hypotheses（直接用已有）
- ❌ 追问已 collected_info 中的信息
- ❌ 建议排查 ruled_out 中的方向
- ❌ 编造排查树和历史案例中没有的操作步骤

---

## 3. Chat 自由问答（新）

### 核心变化

| | v1.x | v2.0 |
|------|------|------|
| taskId prop | ✅ 需要，前端传 | ❌ 不需要 |
| 工单感知 | 只感知当前选中工单 | **感知当前用户所有已派单工单** |
| 诊断信息 | 需要手动 /analyze | **自动注入所有工单的诊断摘要** |
| 无工单场景 | 可以问答 | 可以问答（工程师视角） |
| 优先级判断 | 不支持 | **支持："哪单最急""哪个最难"** |

### Chat API 内部流程

```
POST /api/ai/task/chat/stream {session_id, query}
  │
  ├─ 1. 获取当前用户信息（从 JWT token 解析 username）
  │
  ├─ 2. HTTP GET /api/tasks/?assigned_to=<username>&status=in_progress&size=50
  │     └── 业务后端返回"分配给我"的工单列表
  │     └── 每条含: task_id, title, description, status, priority, diagnosis, ...
  │
  ├─ 3. 构建 Chat Prompt（注入用户工单上下文）
  │     ├── 对话历史（Redis memory，同一 session_id）
  │     ├── 用户工单列表 + 各工单诊断摘要
  │     └── 用户当前消息
  │
  ├─ 4. LLM 流式生成
  │
  └─ 5. 写入 Redis memory
```

### Chat Prompt 设计

```
你是工业移动机器人（AGV/AMR）领域的技术支持专家，服务于接单工程师。

## 你的能力
- 回答 AGV/AMR 技术问题（错误码、配置、故障排查）
- 根据当前用户的工单列表，帮助判断优先级和紧急程度
- 结合工单诊断信息给出针对性建议

## 当前用户的工单
{user_tickets_summary}
  #44946 避让后车不动 [高/进行中]
    诊断: 推测路径规划死锁，已排除网络/硬件故障
  #44958 地图加载不完整 [中/进行中]
    诊断: 推测存储路径异常，已排除网络中断
  #44972 充电桩通信超时 [低/待处理]
    诊断: 推测MQTT消息丢失

## 对话历史
{conversation_history}

## 用户消息
{query}

---
根据用户工单列表和对话历史，给出有帮助的回复。
如果用户在问"哪单最急""优先处理哪个"，结合 priority 和 diagnosis 的置信度/严重性判断。
如果用户问的是纯技术问题，正常回答，不强行关联工单。
如果用户没有工单，以工程师视角回答问题。
```

### 无工单时的行为

- 仍然可以问答
- System Prompt 注入："你当前没有待处理工单。"
- 回答风格：工程技术视角，帮助排查问题或解答疑问

---

## 4. Diagnosis 诊断服务（新）

### 定位

从 ChatPanel 中完全剥离。**不是聊天功能**，是一个独立的后台服务。

### 触发方式

| 触发条件 | 说明 |
|------|------|
| 新派单（status 从 pending_dispatch → in_progress） | 检测到新派单 → 自动触发 → 结果写 task_comments |
| 手动触发 | 工单详情页"重新分析"按钮 |
| 批量触发 | 管理员/定时任务批量重分析 |

### 流程

```
触发（新派单 / 手动 / 批量）
  │
  ├─ 1. 加载工单上下文
  │     └── SQLAlchemy: 读 tasks 表 → diagnosis JSON
  │
  ├─ 2. 三路并行分析（不复诊！）
  │     ├── 排查树结论检索（Qdrant troubleshooting）
  │     ├── 历史工单方案检索（Qdrant task_resolutions）
  │     └── 附件解析（日志/回放）
  │
  ├─ 3. LLM 综合分析 → SolutionDraft
  │
  ├─ 4. 写入 task_comments（created_by=AI任务助手）
  │     └── solution_draft + diagnosed_at + confidence
  │
  └─ 5. 写入 Qdrant task_resolutions（知识闭环）
        └── 向量化方案 → 供后续相似工单检索
```

### 输出格式

**不写独立的 JSON 字段**。AI 诊断结果作为一条 `task_comment` 存入 `task_comments` 表，前端无需改代码即可在工单讨论区渲染。

```sql
-- 判断工单是否已被诊断过：
SELECT 1 FROM task_comments
WHERE task_id = X AND created_by = 'AI任务助手'
LIMIT 1;
```

评论内容格式（Markdown）：

```markdown
## AI 诊断结果

**根因分析**：MAPF v1.1.2 避让算法在特定场景下生成起点=终点的死循环路径...

**建议步骤**：
1. 将 MAPF 算法版本回退至 v1.1.1。操作路径：系统管理 → 算法配置 → MAPF版本选择
2. 设置 avoidance_distance_threshold >= 2.0m

**参考来源**：
- 排查树「车不动，任务状态显示路径规划中」→ MAPF避让算法生成起点=终点路径
- 历史工单 #44123：AGV避让后死锁不动（相似度 0.89）

置信度：85%
```

**选择 task_comments 的原因**：
- 前端已有渲染逻辑，零改动
- 保留历史版本（每次诊断都是一条新评论）
- 所有工程师可见（is_public=True）
- created_by="AI任务助手"，备注后续改为提单人用户名

---

## 5. 诊断状态追踪

### 目的

全局视角判断——"这个工单被 Agent 诊断过没有"。

### 方案：复用 task_comments

**不需要新建追踪表**。直接查询 `task_comments` 表即可：

```sql
-- 是否已诊断
SELECT 1 FROM task_comments WHERE task_id = X AND created_by = 'AI任务助手' LIMIT 1;

-- 最近一次诊断时间
SELECT MAX(created_at) FROM task_comments WHERE task_id = X AND created_by = 'AI任务助手';
```

### 查询方式

- 诊断服务启动时：查 `tasks` 表中 status=in_progress 且 `task_comments` 中无 AI任务助手 记录的工单 → 排队诊断
- Chat API 加载工单时：直接取 `task_comments` 中最新一条 AI任务助手 评论作为诊断上下文
- 工单详情页：前端已经在渲染 `task_comments` 列表，AI 诊断自然出现

---

## 6. API 契约

### 端点一览（v2.0 变更）

| 方法 | 路径 | 说明 | v1→v2 变化 |
|------|------|------|------|
| POST | `/api/ai/task/chat/stream` | Chat 自由问答 SSE | **不再需要 taskId**；内部自动加载用户工单列表 |
| POST | `/api/ai/task/chat` | Chat 自由问答非流式 | 同上 |
| POST | `/api/ai/task/diagnose` | **新增** — 触发单个工单诊断 | 替代旧 `/analyze` |
| POST | `/api/ai/task/diagnose/status` | **新增** — 查询诊断状态 | 读追踪表 |
| POST | `/api/ai/task/submit` | 提交方案→更新工单 | 数据源切换：Ticket→Task |
| POST | `/api/ai/task/list` | 列出工单 | 数据源切换：Ticket→Task |
| GET | `/api/ai/task/health` | 健康检查 | 不变 |

### 废弃的端点（保留兼容，逐步下线）

| 端点 | 命运 |
|------|------|
| `POST /api/ai/task/analyze/stream` | 逻辑移入 `/diagnose`，旧端保留但不再增强 |
| `POST /api/ai/task/analyze` | 同上 |

### 关键请求/响应

#### POST /api/ai/task/chat/stream（v2.0）

```json
// Request — 不再需要 taskId
{ "session_id": "chat_xxx", "query": "我现在有什么急单？" }

// Agent 内部：
// 1. 从 JWT 解析 username
// 2. GET /api/tasks/?assigned_to=<username>&status=in_progress
// 3. 注入工单上下文 → Prompt
// 4. LLM 流式回复
```

#### POST /api/ai/task/diagnose（新）

```json
// Request
{ "task_id": "44946" }

// Response
{ "code": 0, "data": { "task_id": "44946", "status": "diagnosing" } }

// 异步完成后写 task_comments（created_by=AI任务助手） + 更新追踪表
```

#### POST /api/ai/task/diagnose/status（新）

```json
// Request
{ "task_id": "44946" }

// Response
{ "code": 0, "data": {
    "task_id": "44946",
    "diagnosis_status": "diagnosed",
    "confidence": 0.85,
    "diagnosed_at": "2026-07-21T15:30:00"
}}
```

---

## 7. 数据流

### 7.1 Chat 数据流

```
POST /api/ai/task/chat/stream {session_id, query}
  │
  ├─ 1. 解析 username（从 JWT token）
  │
  ├─ 2. HTTP GET /api/tasks/?assigned_to=<username>（业务后端 8400）
  │     └── 返回用户所有待处理工单（含 diagnosis 字段）
  │
  ├─ 3. 构建 Prompt
  │     ├── TASK_CHAT_V2_SYSTEM_PROMPT（含工单列表 + 诊断摘要）
  │     ├── 对话历史（Redis memory）
  │     └── 用户消息
  │
  ├─ 4. LLM.stream → SSE 逐 token
  │
  └─ 5. Redis memory 写入
```

### 7.2 Diagnosis 数据流

```
触发（新派单 / 手动 / 批量）
  │
  ├─ 1. 更新追踪表: status = "diagnosing"
  │
  ├─ 2. _load_task_context(task_id)
  │     └── SQLAlchemy: db.query(Task).filter(id==task_id)
  │           → diagnosis JSON + title/description/priority/attachments
  │
  ├─ 3. _run_analysis（三路并行）
  │
  ├─ 4. LLM 综合分析 → SolutionDraft
  │
  ├─ 5. 写入 task_comments（created_by=AI任务助手）
  │
  └─ 6. 更新追踪表: status = "diagnosed"
```

---

## 8. 前端对接

### v2.0 需要改的前端部分

| # | 改动 | 说明 | 优先级 |
|---|------|------|:---:|
| F1 | **ChatPanel 去掉 taskId prop** | 不再需要传 taskId，ChatAgent 内部自动获取用户工单 | P0 |
| F2 | **ChatPanel SSE body 简化** | `{session_id, query}` — 去掉 task_id 字段 | P0 |
| F3 | **工单详情页渲染诊断** | 从 `task_comments（created_by=AI任务助手）` 读取，静态展示（非聊天窗口） | P0 |
| F4 | **SolutionCard 改为静态展示** | 保留编辑+提交功能，去掉聊天气泡外壳 | P1 |
| F5 | **"重新分析"按钮** | 工单详情页按钮 → `POST /api/ai/task/diagnose` | P1 |

### 不需要改的

| 事项 | 原因 |
|------|------|
| TasksView 工单卡片列表 | 后端按 `assigned_to` 过滤，前端不感知 |
| Nginx 配置 | 已有 `/api/ai/*` → 8401 |
| Workbench store | v2.0 不需要跨视图传递 taskId |

---

## 9. tickets → tasks 迁移（✅ 已完成）

### 状态

2026-07-21 后端同事已完成合并，通过 `ai/core/task_adapter.py` 适配层统一读写。

### 适配层（已实现）

| 函数 | 作用 | 调用方 |
|------|------|------|
| `load_task_context_dict(task_id)` | 读 Task → 解构 diagnosis JSON | `_load_task_context()` |
| `update_task_resolution(task_id, solution, resolution)` | 提交方案 → 写 metadata_info.diagnosis | `submit()` |
| `task_to_dict(task)` | Task → 兼容旧字段名的 dict | `task_list()` |
| `upsert_task(ticket_dict, created_by)` | 提单 Agent 幂等写入 | 提单 Agent |

### 我们的代码已全部切换

| # | 文件 | 状态 |
|---|------|:---:|
| C1 | `_load_task_context()` → `load_task_context_dict()` | ✅ |
| C2 | `submit()` → `update_task_resolution()` | ✅ |
| C3 | `_index_solution()` → `load_task_context_dict()` | ✅ |
| C4 | `task_list()` → `Task` 模型 + `task_to_dict()` | ✅ |
| C5 | `_add_diagnosis_comment()` → 直接写 `TaskComment` | ✅ |

---

## 10. 目录结构

```
ai/agents/AiTaskPlatform/
├── TASK_AGENT_DESIGN.md    # 本文件
├── FEATURE_LIST.md          # 功能清单（给产品经理）
├── PROJECT_OVERVIEW.md      # 项目总览报告
├── __init__.py              # 导出 pipeline + schemas
├── pipeline.py              # AiTaskAgent 核心类
│   ├── chat() / chat_stream()           自由问答（v2.0 改造：注入用户工单列表）
│   ├── diagnose()                       诊断服务（从 analyze 剥离）
│   ├── submit()                         方案提交
│   ├── _load_task_context()            从 tasks 表读工单
│   ├── _run_analysis()                  三路并行分析
│   ├── _build_prompt() / _parse_solution()
│   └── _extract_log_errors()
├── schemas.py               # Pydantic 模型
├── prompts.py               # Prompt 模板（chat v2 / diagnose）
├── analyzer.py              # TaskAnalyzer 三路分析引擎
├── attachment_parser.py     # 附件解析（日志 + 待做：ZIP/文件夹）
├── diagnosis 追踪：复用 task_comments（created_by=AI任务助手）
├── demo.py                  # Mock 数据演示
└── cli_chat.py              # 命令行交互工具
```

---

## 11. 实现状态

### 已完成 ✅

| 功能 | 说明 |
|------|------|
| ChatPanel 自由问答 | 无 taskId 场景的通用问答（v1.0 交付） |
| 三路分析引擎 | 排查树结论 + 历史方案 + 附件解析 |
| SolutionCard | 可编辑方案卡片 |
| 全节点埋点 | 9 个追踪节点 |
| 知识闭环 Layer 1 | Qdrant task_resolutions 读+写 |
| 附件日志解析 | ERROR/WARN 提取 + 截断 |
| PRD 合规 | 版本管理 + 接口登记 |
| CLI 调试工具 | 全流程模拟 |

### 待完成 ⚠️

| 事项 | 优先级 | 说明 |
|------|:---:|------|
| Chat 感知用户全量工单 | **P0** | 去掉 taskId，注入工单列表 |
| 诊断追踪 | **P0** | 复用 task_comments（查询 created_by=AI任务助手） |
| Diagnosis 独立服务 | **P0** | 新派单自动触发 |
| 附件 ZIP/文件夹解析 | P0 | 解压+遍历+识别 |
| tickets→tasks 迁移 | P0 | 等后端加列后改 5 处代码 |
| 知识闭环收尾 | P1 | 联调验证 |

---

## 12. 本周开发任务

### 已完成

1. ✅ 系统任务界面自由问答
2. ✅ 工单深度分析诊断一轮对话版 — 三路并行分析
3. ✅ 工单聊天与自由问答带记忆切换
4. ✅ Agent 全节点埋点 — 9 个追踪节点
5. ✅ 初步知识闭环（Qdrant task_resolutions 读+写）
6. ✅ 附件解析 — txt/log 文件错误提取
7. ✅ ChatPanel 场景切换 + SolutionCard + TasksView 参数透传

### 本周待实现

1. **自由聊天与诊断分离**（P0）— Chat 感知用户全量工单，Diagnosis 独立服务
2. **诊断追踪**（P0）— 复用 task_comments 判断诊断状态
3. **tickets→tasks 迁移**（P0）— 等后端加列，代码切换数据源
4. **新派单自动触发诊断**（P0）— 检测派单→自动生成→写入 task_comments（AI任务助手评论）
5. **附件解析升级**（P0）— 加入 ZIP/文件夹处理
6. **知识闭环收尾**（P1）— collection 初始化验证 + 日志深度分析升级

---

## 13. 能力分层路线图

（从 v1.2 保留，不变）

```
Layer 0: 基础诊断（✅ 已完成）    Layer 1: 证据链（👈 当前）   Layer 2: 协作推理              Layer 3: 知识闭环
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ ✅ 排查树结论检索      │  │ ✅ 历史工单方案检索     │  │ 🔲 多步假设验证         │  │ 🔲 方案效果评估         │
│ ✅ 附件日志解析        │  │ 🔲 日志深度分析        │  │ 🔲 配置参数感知         │  │ 🔲 方案回写 Qdrant     │
│ ✅ LLM 直接推断        │  │ 🔲 对话历史线索提取    │  │ 🔲 相似工单聚合告警     │  │ 🔲 版本-缺陷知识库      │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘
```
