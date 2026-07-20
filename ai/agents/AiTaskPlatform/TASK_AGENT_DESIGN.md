# AiTaskPlatform — 任务 Agent 设计文档

> 版本：1.0 | 日期：2026-07-20
>
> **本文件是 AiTaskPlatform 的权威设计文档**，供开发时参考和每次新对话恢复上下文。
>
> **当前状态**：AI 侧核心功能 + 前端集成已交付。Qdrant task_resolutions、附件回放解析为后续迭代。

---

## 目录

1. [定位与目标](#1-定位与目标)
2. [职责边界（核心）](#2-职责边界核心)
3. [交互流程](#3-交互流程)
4. [目录结构](#4-目录结构)
5. [API 契约](#5-api-契约)
6. [数据流](#6-数据流)
7. [Pipeline 设计](#7-pipeline-设计)
8. [LLM Prompt 设计](#8-llm-prompt-设计)
9. [历史工单方案检索](#9-历史工单方案检索)
10. [前端对接](#10-前端对接)
11. [与提单 Agent 的关系](#11-与提单-agent-的关系)
12. [实现状态](#12-实现状态)
13. [后续迭代](#13-后续迭代)

---

## 1. 定位与目标

### 一句话

**面向接单工程师的 AI 助手——基于工单已有诊断信息，检索知识库方案结论和历史案例，生成结构化解决方案草稿，人工校准后提交完成。**

### 三 Agent 全景

```
                    ai/agents/
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   AiDiagnosisPlatform  AiTaskPlatform  AiDataAnalysisPlatform
   （需求视角）         （供给视角）     （管理视角）
   客户报障+诊断+提单   工程师接单+排查   数据看板+风险分析
        │                    │
        │  diagnosis JSON    │  读 diagnosis（不复诊）
        ├──────────────────►│
        │                    │
        │            ┌───────┘
        │            ▼
        │    生成方案草稿 → 校准 → 提交
        │            │
        └────────────┘  tickets 表闭环
```

### 对比速查

| | 提单 Agent | 任务 Agent | 数据分析平台 |
|---|---|---|---|
| 使用者 | 客户/现场人员 | 接单工程师 | 管理人员 |
| 入口 | 我要摇人 | 系统任务 | 后台管理 |
| 触发 | 用户描述问题 | 工程师选中工单 或 自由问答 | 选择分析类型 |
| 知识源 | 5 路 KB 检索（用于诊断） | diagnosis JSON + 排查树结论 + 历史工单方案 | 用户提供的结构化数据 |
| 输出 | 对话式引导 + 工单 | chat: 通用问答 / analyze: 结构化方案草稿 | 分析报告 |
| 闭环 | submit → 写入 tickets 表 | submit → 更新 tickets 表 + Qdrant 回写 | — |

---

## 2. 职责边界（核心）

### 提单 Agent 交付给工单的 diagnosis JSON

```json
{
  "problem_summary": "避让后车不动，路径起点=终点",
  "hypotheses": ["路径规划死锁", "MAPF 算法异常"],
  "ruled_out": ["网络通信异常", "车辆硬件故障"],
  "collected_info": {"robot_type": "潜伏车", "error_time": "14:40"},
  "rounds": 2
}
```

### 任务 Agent 不重复做的事

| 直接用的 | 用法 |
|------|------|
| `hypotheses` | 直接作为验证起点，不再重新假设 |
| `ruled_out` | **跳过**，禁止让工程师排查已排除的方向 |
| `collected_info` | 直接引用，不再追问已收集的信息 |
| `problem_summary` | 用于检索时的查询文本 |

### 任务 Agent 新增做的

| 能力 | 说明 |
|------|------|
| 通用问答 (chat) | 无 taskId 时回答任何 AGV/AMR 技术问题 |
| 工单分析 (analyze) | 有 taskId 时三路并行分析→生成结构化方案草稿 |
| 方案提交 (submit) | 编辑后提交→更新 tickets 表 + Qdrant 回写 |

### 铁律

- ❌ 重新做 5 路 KB 全文检索（提单 Agent 已经做过）
- ❌ 重新推断 hypotheses（直接用已有）
- ❌ 追问已 collected_info 中的信息
- ❌ 建议排查 ruled_out 中的方向
- ❌ 编造排查树和历史案例中没有的操作步骤

---

## 3. 交互流程

### 实际前端体验

```
工程师打开「系统任务」页面（TasksView）
  │
  ├─ 顶栏 ChatPanel：默认无 taskId → chat/stream 自由问答
  │    "这个错误码 E1601 是什么意思？"
  │
  ├─ 底栏工单卡片列表（GET /api/tasks/，业务后端查询 tickets 表）
  │    点击卡片 → ChatPanel taskId 绑定 → 自动注入诊断摘要
  │
  ▼ 选中工单后
  │    ChatPanel 切到 analyze/stream
  │    输入"帮我分析"→ SSE 流式输出方案草稿
  │    → SolutionCard 可编辑渲染
  │    → 工程师校准 → submit → tickets 表状态更新
  │
  ├─ 关掉详情 Popup → taskId 清空 → 回到 chat/stream
  │    同一 session_id 上下文不丢
```

---

## 4. 目录结构

```
ai/agents/AiTaskPlatform/
├── TASK_AGENT_DESIGN.md   # 本文件
├── __init__.py             # 导出 pipeline + schemas
├── pipeline.py             # AiTaskAgent 核心类 (chat/analyze/submit + _load_task_context)
├── schemas.py              # 9 个 Pydantic 模型（请求/数据/响应）
├── prompts.py              # 3 个 prompt 模板（analyze / chat / task_list）
├── analyzer.py             # TaskAnalyzer: 三路并行分析编排
├── attachment_parser.py    # 日志 ERROR/WARN 提取 + 截断
├── demo.py                 # Mock 数据演示（供开发调试）
└── cli_chat.py             # 命令行交互（供开发调试，不提交）
```

**依赖复用**：

| 依赖 | 来源 | 说明 |
|------|------|------|
| LLM 客户端 | `ai.core.llm` | `get_llm_client()` 单例 |
| Embedding | `ai.core.embed` | `get_embed_client()` 单例 |
| 排查树检索 | `ai.core.retrieval.retrieve_troubleshooting()` | 复用已有方法 |
| 会话记忆 | `ai.core.memory` | `get_memory_manager()` 单例 |
| 工单数据 | `tickets` 表（SQLAlchemy 直读） | AI 模块自有数据，不调后端 API |

---

## 5. API 契约

### 端点一览（7 个）

| 方法 | 路径 | 说明 | 场景 |
|------|------|------|------|
| POST | `/api/ai/task/chat/stream` | 自由问答 SSE 流式 | 无 taskId / 通用技术讨论 |
| POST | `/api/ai/task/chat` | 自由问答非流式 | 同上 |
| POST | `/api/ai/task/analyze/stream` | 工单分析 SSE 流式 | 有 taskId: 三路分析→方案草稿 |
| POST | `/api/ai/task/analyze` | 工单分析非流式 | 同上 |
| POST | `/api/ai/task/submit` | 确认方案→更新 tickets + Qdrant | 工程师编辑后提交 |
| POST | `/api/ai/task/list` | 列出当前用户待处理工单 | Agent 视角工单列表 |
| GET | `/api/ai/task/health` | 健康检查 | — |

### 路由规则

```
ChatPanel send():
  无 taskId → /api/ai/task/chat/stream  (自由问答)
  有 taskId → /api/ai/task/analyze/stream (工单分析→SolutionCard)
  call 场景 → /api/ai/qa/ask/stream (提单 Agent，不变)
```

### 关键请求/响应

#### POST /api/ai/task/analyze/stream

```json
// Request
{ "task_id": "44946", "session_id": "task_44946_20260720" }

// SSE 事件流
event: status  → {stage: "loading_context"}
event: status  → {stage: "retrieving"}
event: status  → {stage: "generating"}
event: first_token → {ms: 1234}
data: {token: "根"} ...
event: result  → {root_cause_analysis, suggested_actions, references, confidence, needs_more_info}
event: done    → {total_ms: 5678}
```

#### POST /api/ai/task/submit

```json
// Request
{ "task_id": "44946", "session_id": "...", "final_solution": {...}, "resolution": "resolved" }

// Response
{ "code": 0, "data": { "task_id": "44946", "solution_indexed": false, "ticket_updated": true } }
```

---

## 6. 数据流

```
POST /api/ai/task/analyze/stream
  │
  ├─ _load_task_context(task_id)
  │     └── SQLAlchemy: db.query(Ticket).filter(id==task_id)
  │           → title, description, type, priority, attachments
  │           → diagnosis JSON → problem_summary, hypotheses, ruled_out, collected_info
  │
  ├─ _run_analysis (三路并行)
  │     ├── 排查树结论检索 (Qdrant troubleshooting → 结论节点)
  │     ├── 历史工单方案检索 (Qdrant task_resolutions, 未实现→占位)
  │     └── 附件解析 (本地文件/HTTP, 仅日志)
  │
  ├─ _build_prompt → USER_PROMPT_TEMPLATE (diagnosis + 检索结果)
  │
  ├─ LLM.stream → SSE 逐 token 透传
  │
  ├─ _parse_solution → SolutionDraft
  │
  └─ _save_analysis_context → Redis memory (同一 session_id 跨 chat/analyze 共享)
```

### submit 数据流

```
POST /api/ai/task/submit
  │
  ├─ Qdrant 回写 (placeholder, 不阻塞)
  │
  └─ tickets 表直接更新
        ├── Ticket.status = "resolved"
        ├── Ticket.diagnosis["solution"] = {...}
        └── Ticket.diagnosis["resolved_by_agent"] = True
```

---

## 7. Pipeline 设计

### 核心类：`AiTaskAgent`

```python
class AiTaskAgent:
    """任务 Agent：自由问答 + 工单分析 + 方案提交"""
    
    # 懒加载的 AI 核心单例
    _llm_client: LLMClient          # ai.core.llm
    _retriever: RetrievalService    # ai.core.retrieval
    _memory: MemoryManager          # ai.core.memory
    
    async def chat(session_id, query, task_id?, ...) → str
    async def chat_stream(session_id, query, task_id?, ...) → SSE
    async def analyze(TaskAnalyzeRequest) → SolutionDraft
    async def analyze_stream(TaskAnalyzeRequest) → SSE
    async def submit(task_id, session_id, draft, resolution) → dict
```

### 核心数据类

```python
class SolutionDraft(BaseModel):
    root_cause_analysis: str       # 一句话结论 + 推理链
    suggested_actions: list[str]   # 优先级排序，每步具体可执行
    references: list[str]          # 排查树节点 / 历史工单 ID
    confidence: float              # (0~1)
    needs_more_info: bool          # 真正缺信息才为 True

class TaskContext(BaseModel):
    task_id, title, description, task_type, priority, status, source
    problem_summary, hypotheses, ruled_out, collected_info
    fault_code, robot_type, location, attachments, diagnosis_rounds
```

### 外部依赖

| 依赖 | 方式 | 说明 |
|------|------|------|
| `tickets` 表 | SQLAlchemy SessionLocal() | 工单全量数据（diagnosis JSON 在内） |
| Redis | `ai.core.memory` | 对话上下文跨 chat/analyze 共享 |
| Qdrant | `ai.core.retrieval` | 排查树结论检索（已就绪）；task_resolutions（未实现） |
| DeepSeek | `ai.core.llm` | 两种 system prompt 切换 |

---

## 8. LLM Prompt 设计

### 两种模式

| 模式 | System Prompt | 触发条件 | 输出 |
|------|-------------|---------|------|
| `TASK_CHAT_SYSTEM_PROMPT` | 通用技术支持专家 | 无 taskId | 自然语言回复 |
| `TASK_AGENT_SYSTEM_PROMPT` | 方案生成器（有铁律） | 有 taskId + analyze | 结构化 JSON SolutionDraft |

### 铁律（analyze 模式）

1. 禁止重新诊断
2. 禁止建议排查 ruled_out 中的方向
3. 禁止追问 collected_info 中已有的信息
4. 禁止编造排查树和历史案例中没有的操作步骤

---

## 9. 历史工单方案检索

### 新 Collection：`task_resolutions`（未实现）

| 字段 | 内容 |
|------|------|
| `task_id` | 工单编号 |
| `title` | 工单标题 |
| `problem_summary` | 问题描述（来自 diagnosis） |
| `root_cause` | 最终确认的根因 |
| `solution_steps` | 解决步骤 |
| `engineer_note` | 工程师备注 |
| `fault_code` | 关联故障码（如有） |
| `robot_type` | 关联车型（如有） |

### 实现计划

- `RetrievalService.retrieve_task_resolutions(query, top_k=3)` — 新增方法
- `submit()` 中回写向量化方案到 Qdrant
- 批量迁移历史已解决工单

---

## 10. 前端对接

### 已完成的改动

| # | 文件 | 改动 | 状态 |
|---|------|------|:---:|
| F1 | `ChatPanel.tsx` | scene + taskId 三元路由 (chat/analyze/qa) | ✅ |
| F2 | `ChatPanel.tsx` | Message 接口扩展 solution_draft 字段 | ✅ |
| F3 | `ChatPanel.tsx` | taskId 变化时自动注入诊断摘要 | ✅ |
| F4 | `SolutionCard.tsx` | 新建——可编辑方案卡片组件 | ✅ |
| F5 | `TasksView.tsx` | 传 taskId/taskTitle/taskDescription 给 ChatPanel | ✅ |
| F6 | `ChatPanel.tsx` | handleSubmitSolution + handleReanalyze | ✅ |

### 工单列表数据源

- 前端 TasksView 的工单卡片列表：调 `/api/tasks/`（业务后端，端口 8400）
- 后端同事查询 `tickets` 表返回
- 任务 Agent 不负责工单卡片列表的前端渲染

### 不需要改的

| 事项 | 原因 |
|------|------|
| Nginx 配置 | 已有 `/api/ai/*` → 8401 转发 |
| Workbench store | taskId 通过 ChatPanel prop 透传 |
| 对话持久化 sceneType | 当前 consultation 可复用 |

---

## 11. 与提单 Agent 的关系

```
提单 Agent (AiDiagnosisPlatform)        任务 Agent (AiTaskPlatform)
        │                                       │
        │  submit(): 写入 tickets 表              │  chat/analyze: 读 tickets 表
        │  ├── diagnosis JSON                   │  ├── 读 diagnosis（不复诊）
        │  ├── title/description/type/priority  │  ├── 排查树结论检索
        │  └── attachments                      │  ├── 附件解析
        │                                       │  └── 输出 SolutionDraft
        │                                       │
        │  tickets.diagnosis ─────────►         │  (同表同字段，单向流转)
        │                                       │
        │                                       │  submit(): 更新 tickets 表
        │                                       │  ├── status = "resolved"
        │                                       │  └── diagnosis["solution"] = {...}
        └───────────────────────────────────────┘
```

**两个 Agent 通过 `tickets` 表共享数据，不直接耦合。**

---

## 12. 实现状态

### 已完成 ✅

| Phase | 内容 | 文件 |
|:---:|------|------|
| 1 | 骨架搭建 | `schemas.py` / `prompts.py` / `pipeline.py` / `__init__.py` |
| 2 | 分析引擎 | `analyzer.py` / `attachment_parser.py` |
| 3 | API 路由 | `ai/api/router.py`: 7 个端点 + `ai/run.py` 挂载 |
| 4 | 前端集成 | `ChatPanel.tsx` / `SolutionCard.tsx` / `TasksView.tsx` |
| — | DB 直读 | `_load_task_context()` 用 SQLAlchemy 读 tickets 表 |
| — | 上下文连续 | `chat()` / `chat_stream()` 写入 Redis memory |
| — | bugfix | 重复 return ctx（已修复） |

### 待完成 ⚠️

| 事项 | 优先级 | 说明 |
|------|:---:|------|
| `retrieve_task_resolutions()` | P0 | Qdrant 新 collection + 检索方法 |
| `_index_solution()` 实现 | P0 | 当前是 placeholder print |
| 附件回放解析 | P2 | `attachment_parser.py` 已有骨架 |
| 前后端联调 | P1 | 需 MySQL + Qdrant 就绪 |

---

## 13. 后续迭代

| 迭代 | 内容 |
|:---:|------|
| 1 | Qdrant task_resolutions collection → `retrieve_task_resolutions()` 实现 |
| 2 | 附件解析扩展：回放文件路径分析 |
| 3 | 前端工单列表改走 AI 模块 list 端点（定数据源） |
| 4 | 批量迁移历史工单 → Qdrant |
