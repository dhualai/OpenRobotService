# AiTaskPlatform — 任务 Agent 设计文档

> 版本：0.3（设计草案）| 日期：2026-07-20
>
> **本文件是 AiTaskPlatform 的权威设计文档**，供开发时参考和每次新对话恢复上下文。

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
10. [与前端的对接约定](#10-与前端的对接约定)
11. [与提单 Agent 的关系](#11-与提单-agent-的关系)
12. [实现计划](#12-实现计划)
13. [团队对接协调清单](#13-团队对接协调清单)

---

## 1. 定位与目标

### 一句话

**面向接单工程师的 AI 助手——基于工单已有诊断信息，检索知识库方案结论和历史案例，生成结构化解决方案草稿，人工校准后提交完成。**

### 命名说明

`AiTaskPlatform` — "Task" 指本 Agent 的核心能力是处理「工单解决方案生成」任务，对应三视角中的**供给视角（系统任务）**。与现有 `AiDiagnosisPlatform`（诊断能力）`AiDataAnalysisPlatform`（数据分析能力）同属 `Ai{Capability}Platform` 命名模式。

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
        │  diagnosis JSON    │  读 diagnosis（不重诊）
        ├──────────────────►│
        │                    │
        │            ┌───────┘
        │            ▼
        │    生成方案草稿 → 校准 → 提交
        │            │
        └────────────┘  方案回写 Qdrant（闭环）
```

### 三 Agent 对比速查

| | 提单 Agent | 任务 Agent | 数据分析平台 |
|---|---|---|---|
| 使用者 | 客户/现场人员 | 接单工程师 | 管理人员 |
| 入口 | 我要摇人 | 系统任务 | 后台管理 |
| 触发 | 用户描述问题 | 工程师选中工单 | 选择分析类型 |
| 知识源 | 5 路 KB 检索（用于诊断） | diagnosis JSON + 排查树结论 + 历史工单方案 | 用户提供的结构化数据 |
| 输出 | 对话式引导 + 工单 | **结构化方案草稿（可编辑）** | 分析报告 |
| 闭环 | submit → 生成工单 | resolve → **方案回写 Qdrant** | — |

---

## 2. 职责边界（核心）

### 提单 Agent 做了什么（任务 Agent 不再重复）

```
客户说"车不动了"
  ├── 5 路 KB 检索 → 匹配症状
  ├── LLM 多轮追问 → 推断 hypotheses、排除 ruled_out、收集 collected_info
  └── 生成工单 → diagnosis JSON 写入 tickets 表
```

提单 Agent 交付给工单的 **diagnosis JSON**：
```json
{
  "problem_summary": "避让后车不动，路径起点=终点",
  "hypotheses": ["路径规划死锁", "MAPF 算法异常"],
  "ruled_out": ["网络通信异常", "车辆硬件故障"],
  "collected_info": {"robot_type": "潜伏车", "error_time": "14:40", "fault_code": ""},
  "rounds": 2
}
```

### 任务 Agent 的输入和不允许做的事

**可以直接用的**（提单 Agent 已验证过）：
| 信息 | 用法 |
|------|------|
| `hypotheses` | 直接作为验证起点，不再重新假设 |
| `ruled_out` | **跳过**，禁止让工程师排查已排除的方向 |
| `collected_info` | 直接引用，不再追问已收集的信息 |
| `problem_summary` | 用于检索时的查询文本 |
| `fault_code` / `robot_type` / `location` | 附加检索条件 |

**任务 Agent 新增做的事**（提单 Agent 没做的）：
| 能力 | 说明 |
|------|------|
| 🔍 查排查树结论节点 | 提单 Agent 用排查树做**分流+步骤引导**；任务 Agent 需要的是**结论节点的根因+方案** |
| 📋 查历史工单方案 | 语义检索 Qdrant `task_resolutions` collection |
| 📎 解析附件 | 日志提取关键错误 + 回放提取路径/状态异常 |
| 📝 生成方案草稿 | 不是新一轮诊断，而是基于已有推论 + 结论 + 案例 → 直接出方案 |

**绝对禁止**：
- ❌ 重新做 5 路 KB 全文检索（提单 Agent 已经做过）
- ❌ 重新推断 hypotheses（直接用已有）
- ❌ 追问已 collected_info 中的信息
- ❌ 建议排查 ruled_out 中的方向
- ❌ 说"请提供更多信息"除非确实所有材料都不足以出方案（且此时标记 `needs_more_info: true`）

### 两个 Agent 的分界线

```
┌─────────────────── 提单 Agent ───────────────────┐
│                                                   │
│  症状 → 检索 KB → 推断 → 追问 → 排除 → 收集      │
│                                                   │
│  目标：把模糊的症状变成结构化的 diagnosis         │
│                                                   │
└──────────────────────┬────────────────────────────┘
                       │ diagnosis JSON
                       ▼
┌─────────────────── 任务 Agent ───────────────────┐
│                                                   │
│  已有推论 + 排查树结论 + 历史方案 + 附件 → 方案   │
│                                                   │
│  目标：把已有的诊断转化成可执行的解决方案         │
│                                                   │
└──────────────────────────────────────────────────┘
```

---

## 3. 交互流程

```
工程师打开「系统任务」页面
  │
  ├─ Step 0: Agent 打招呼 + 列出待处理工单
  │    "你当前有 3 个待处理工单：
  │     1. #44946 避让后车不动 [高] — 可疑：路径规划死锁
  │     2. #44958 地图加载不完整 [中] — 可疑：SLAM 定位点丢失
  │     3. #44972 充电桩通信超时 [低] — 可疑：MQTT 心跳断开
  │     请选择要处理的工单号"
  │
  ▼ 工程师选择 #44946
  │
  ├─ Step 1: 加载工单上下文
  │    ├── 调后端 GET /api/tasks/{task_id}（端口 8400）
  │    │     → title, description, status, type, priority,
  │    │       attachments, source, assigned_to, metadata_info, ...
  │    └── 从 diagnosis JSON 获取诊断信息
  │         （通过 session_id 关联 tickets 表 或 metadata_info 字段）
  │
  ├─ Step 2: 三路并行分析（不复诊！）
  │    │
  │    ├── 排查树结论检索
  │    │     └── 用 hypotheses + problem_summary 查排查树
  │    │         → 返回匹配症状的结论节点（根因 + 方案）
  │    │
  │    ├── 历史工单方案检索 (Qdrant task_resolutions)
  │    │     └── 用 problem_summary + fault_code + robot_type 语义检索
  │    │         → 返回相似已解决工单的最终方案
  │    │
  │    └── 附件解析（如有）
  │          ├── 日志 → 正则提取 ERROR/WARN + 时间线
  │          ├── 回放 → 路径/状态关键数据提取
  │          └── 无附件 → 跳过
  │
  ├─ Step 3: LLM 综合分析 → 生成「解决方案草稿」
  │    Prompt 注入:
  │    ├── diagnosis (hypotheses / ruled_out / collected_info)
  │    ├── 排查树结论（根因 + 方案）
  │    ├── 历史工单方案
  │    └── 附件分析摘要
  │
  │    输出: SolutionDraft JSON（SSE 流式逐 token）
  │
  ├─ Step 4: 工程师编辑校准
  │    前端渲染可编辑草稿 → 修改 → 提交
  │
  └─ Step 5: 方案提交（Agent 不直写 DB！）
       ├── AI 侧：向量化方案 → 写入 Qdrant task_resolutions collection
       └── 后端侧：调 PUT /api/tasks/{task_id} → status=resolved + solution 入 metadata_info
```

### 提交时的边界约定（关键）

```
┌────── AI 服务 (8401) ──────┐     ┌──── 业务后端 (8400) ────┐
│                              │     │                          │
│  POST /api/ai/task/submit   │     │  PUT /api/tasks/{id}    │
│  ├── 向量化方案 → Qdrant    │     │  ├── 状态机校验          │
│  └── HTTP 调用 ─────────────┼────►│  ├── status → resolved   │
│                              │     │  ├── 写 audit trail     │
│                              │     │  └── 触发通知            │
└──────────────────────────────┘     └──────────────────────────┘

Agent 只管：方案生成 + 向量化入库（Qdrant）
后端只管：状态变更 + 审计 + 通知（MySQL）
```

---

## 4. 目录结构

```
ai/agents/AiTaskPlatform/
├── TASK_AGENT_DESIGN.md   # 本文件（设计文档）
├── __init__.py             # 导出 pipeline + schemas
├── pipeline.py             # 核心流水线：上下文加载 → 三路分析 → LLM 生成
├── schemas.py              # Pydantic 模型：TaskContext / SolutionDraft
├── prompts.py              # System prompt + 方案生成 prompt 模板
├── analyzer.py             # 三路分析编排：排查树结论 + 历史方案 + 附件解析
└── attachment_parser.py    # 附件解析：日志 → 关键事件 + 回放 → 路径分析
```

**依赖复用关系**:

| 依赖 | 来源 | 说明 |
|------|------|------|
| LLM 客户端 | `ai.core.llm` | 复用 |
| Embedding | `ai.core.embed` | 复用 |
| 排查树单独检索 | `ai.core.retrieval.retrieve_troubleshooting()` | 复用已有方法 |
| 会话记忆 | `ai.core.memory` | 复用 |
| 历史工单方案检索 | `ai.core.retrieval` 新增 `retrieve_task_resolutions()` | 新方法 |
| 工单数据 | **后端 REST API** (`GET/PUT /api/tasks/{id}`，端口 8400) | HTTP 调用 |

### 版本管理

本文件即任务 Agent 的权威设计文档。每次模型/Prompt/流程变更记录版本号和日期（见顶部版本行）。PRD 要求的 `docs/agents/dispatch-agent.md` 由本文件导出关键版本日志，与团队协调后创建。

---

## 5. API 契约

### 5.1 路由挂载

```python
# ai/api/router.py 新增
task_agent_router = APIRouter(prefix="/api/ai/task", tags=["AI任务助手"])
```

挂载到 `run.py`：
```python
from ai.api import qa_router, chat_router, memory_router, assigner_router, task_agent_router
app.include_router(task_agent_router)
```

### 5.2 端点一览

| 方法 | 路径 | 说明 | SSE |
|------|------|------|:---:|
| POST | `/api/ai/task/list` | 列出当前用户待处理工单 (Agent 视角) | — |
| POST | `/api/ai/task/analyze` | 分析指定工单 → 生成方案草稿 (非流式) | — |
| POST | `/api/ai/task/analyze/stream` | 同上，SSE 流式输出 | ✅ |
| POST | `/api/ai/task/submit` | 确认方案 → Qdrant 回写 + 调后端 API 更新状态 | — |
| GET | `/api/ai/task/health` | 健康检查 | — |

### 5.3 请求/响应详情

#### POST /api/ai/task/analyze/stream

```json
// Request
{
  "task_id": "44946",
  "session_id": "task_44946_20260720"
}
```

SSE 事件流：

```json
// event: status → {stage: "loading_context"}
// event: status → {stage: "retrieving"}
// event: status → {stage: "generating"}
// event: first_token → {ms: 1234}
// data: {token: "根"} ... (逐 token 流式)
// event: result → 完整结构化结果
{
  "root_cause_analysis": "...",
  "suggested_actions": ["步骤1", "步骤2"],
  "references": [
    "🔍 排查树「车不动，路径规划中」→ 结论节点: 路径死循环，回退 mapf 版本",
    "📋 相似工单 #44123：同一算法版本 v1.1.2 避让死锁，已通过回退解决"
  ],
  "confidence": 0.82,
  "needs_more_info": false,
  "attachment_analysis": {
    "has_logs": true,
    "log_summary": "14:40:02 路径起点=终点(78.867,-0.122)",
    "has_replay": true,
    "replay_summary": "回放确认：车辆在原位置未移动"
  }
}
// event: done → {total_ms: 5678}
```

#### POST /api/ai/task/submit

```json
// Request
{
  "task_id": "44946",
  "session_id": "task_44946_20260720",
  "final_solution": {
    "root_cause_analysis": "工程师编辑后的根因...",
    "suggested_actions": ["工程师编辑后的步骤..."],
    "engineer_note": "已联系算法组确认，下个版本修复"
  },
  "resolution": "resolved"
}

// Response（Agent 做的事：Qdrant 回写 + 调后端 API）
{
  "code": 0,
  "data": {
    "task_id": "44946",
    "status": "resolved",
    "solution_indexed": true,
    "backend_updated": true,
    "message": "方案已保存，工单已标记为已解决"
  }
}
```

**内部实现**（两个独立操作，任一失败不阻塞另一个）：

```python
async def submit(...) -> dict:
    result = {"solution_indexed": False, "backend_updated": False}

    # 1. Qdrant 回写（AI 服务负责）
    try:
        vector = await embed_client.embed(solution_text)
        await qdrant.upsert("task_resolutions", vector, metadata)
        result["solution_indexed"] = True
    except Exception:
        pass  # 不阻塞

    # 2. 调后端 API 更新状态（业务后端负责）
    try:
        resp = await httpx.put(
            f"{BACKEND_URL}/api/v1/tasks/{task_id}",
            json={"status": resolution, "metadata_info.solution": solution}
        )
        if resp.status_code == 200:
            result["backend_updated"] = True
    except Exception:
        pass  # 不阻塞

    return {"code": 0, "data": result}
```

---

## 6. 数据流

```
POST /api/ai/task/analyze/stream {task_id, session_id}
        │
        ▼
┌──────────────────────────────────────┐
│ AiTaskAgent.analyze()                │
│                                      │
│ 1. 加载工单上下文                     │
│    ├── GET /api/tasks/{task_id}      │ ← 业务后端 REST (8400)
│    │     → title, description, type, │
│    │       priority, attachments,    │
│    │       source, metadata_info     │
│    └── 取 diagnosis JSON             │
│          (session_id → tickets 表    │
│           或 metadata_info 字段)     │
│                                      │
│ 2. 三路并行分析                       │
│    ├── 排查树结论检索                 │ ← Qdrant (troubleshooting)
│    │     └── 只取结论节点的根因+方案   │
│    ├── 历史工单方案检索               │ ← Qdrant (task_resolutions)
│    └── 附件解析 (有则做)             │ ← attachment_parser
│                                      │
│ 3. 构建 Prompt                       │
│ 4. LLM.stream(prompt)                │ ← DeepSeek API
│ 5. parse → SolutionDraft             │
│ 6. save to memory (Redis)            │ ← 多轮编辑上下文
│                                      │
│ 返回 SSE → 前端渲染                   │
└──────────────────────────────────────┘
```

### 提交数据流（两个服务协作）

```
POST /api/ai/task/submit (AI 服务 8401)
        │
        ├── 1. 向量化方案 → Qdrant task_resolutions (AI 侧)
        │
        └── 2. HTTP PUT /api/v1/tasks/{task_id} (业务后端 8400)
                 │
                 ├── 状态机校验 (ALLOWED_TRANSITIONS)
                 ├── status → resolved + resolved_at = now()
                 ├── metadata_info.solution = {...}
                 ├── audit trail (updated_at, update history)
                 └── 通知 (微信模板消息)
```

---

## 7. Pipeline 设计

### 核心类：`AiTaskAgent`

```python
class AiTaskAgent:
    """任务 Agent：分析工单 → 生成方案（不复诊！）"""

    _llm_client: LLMClient
    _retriever: RetrievalService
    _memory: MemoryManager
    _backend_url: str = "http://localhost:8400"  # 业务后端地址

    async def analyze(request: TaskAnalyzeRequest) -> SolutionDraft
    async def analyze_stream(request: TaskAnalyzeRequest) -> AsyncGenerator
    async def submit(session_id: str, draft: SolutionDraft, resolution: str) -> dict
```

### 核心数据类

```python
class TaskAnalyzeRequest(BaseModel):
    task_id: str
    session_id: str

class SolutionDraft(BaseModel):
    root_cause_analysis: str      # 根因分析（引用 diagnosis 推理链 + 排查树结论 + 历史案例）
    suggested_actions: list[str]  # 建议步骤（优先级排序，每步具体可执行）
    references: list[str]         # 参考来源（排查树节点 / 历史工单 ID）
    confidence: float             # 置信度 (0~1)
    needs_more_info: bool         # 真正需要额外信息时才为 true

class TaskContext(BaseModel):
    """工单完整上下文（只读，不复诊；从后端 REST API 获取）"""
    task_id: str
    title: str
    description: str
    task_type: str
    priority: str
    status: str
    source: str                   # manual / zentao / ai_agent / ...
    assigned_to: str | None
    attachments: list[dict]
    metadata_info: dict | None
    # 来自 diagnosis JSON（提单 Agent 交付，通过 session_id 关联）
    problem_summary: str = ""
    hypotheses: list[str] = []
    ruled_out: list[str] = []
    collected_info: dict = {}
    fault_code: str = ""
    robot_type: str = ""
    location: str = ""
    diagnosis_rounds: int = 0
```

---

## 8. LLM Prompt 设计

### 核心原则

```
你不做诊断 —— 提单 Agent 已经做完了。
你只做一件事：基于已有诊断，找出解决方案。

输入给你的是：
  - 提单 Agent 的推论 (hypotheses / ruled_out / collected_info)
  - 排查树匹配到的结论节点（根因 + 方案）
  - 历史相似工单的最终解决方案
  - 附件解析摘要（日志/回放）

你要输出的是：
  - 根因分析（串联以上信息 → 一句话结论 + 推理链）
  - 建议步骤（可执行、排优先级）
  - 参考来源
```

### 系统提示词（草案）

```markdown
你是工业移动机器人（AGV/AMR）领域的技术支持专家，服务于接单工程师。

## 你的角色

你是**方案生成器**，不是诊断助手。提单 Agent 已经完成了初步诊断，你现在要做的是把已有的推论转化为可执行的解决方案。

## 输入材料（按优先级）

1. **提单 Agent 诊断结果**（第一优先级）
   - `hypotheses`：推测的根因方向 —— **从这些开始验证，不要重新推断**
   - `ruled_out`：已排除的方向 —— **绝对禁止让工程师去排查这些**
   - `collected_info`：已收集的信息 —— **直接引用，不要追问**
2. **知识库排查树结论节点**：匹配到的根因 + 方案，直接引用
3. **历史相似工单方案**：最有价值的参考，标注工单 ID
4. **附件分析摘要**：日志关键错误 / 回放异常数据

## 输出要求

输出 JSON 格式：

```json
{
  "root_cause_analysis": "一句话结论 + 推理链（引用了什么信息得出）",
  "suggested_actions": ["步骤1 - 具体操作", "步骤2 - 具体操作"],
  "references": ["来源1", "来源2"],
  "confidence": 0.85,
  "needs_more_info": false
}
```

- 根因分析：先给结论，再给推理链（X 信息 + Y 排查树结论 + Z 历史案例 → 根因）
- 建议步骤：按优先级排序，每步具体可执行（不是"检查一下"，而是"去 XX 界面看 YY 字段是否为 ZZ"）
- 置信度：诊断信息充分 + 排查树精确匹配 + 历史案例高度相似 → ≥0.8
- needs_more_info：**仅当** hypotheses 为空 + 排查树无匹配 + 无历史案例 + 无附件分析时，才设为 true（极其罕见）

## 铁律

1. **禁止重新诊断**。不要问"这个错误码是什么意思"，提单 Agent 已经查过了
2. **禁止建议排查 ruled_out 中的方向**
3. **禁止追问 collected_info 中已有的信息**
4. **禁止编造**排查树和历史案例中没有的操作步骤
```

### 用户 Prompt 模板

```markdown
## 工单信息
标题: {title}
描述: {description}
类型: {task_type} | 优先级: {priority}
来源: {source}

## 提单 Agent 诊断结果（直接使用，不再重新推断）
问题概述: {problem_summary}
推测原因: {hypotheses}
已排除: {ruled_out}
已收集信息: {collected_info}
诊断轮数: {rounds}

{fault_code / robot_type / location}

## 排查树匹配的结论节点（根因 + 方案）
{troubleshooting_conclusions}

## 历史相似工单方案（最直接参考）
{historical_solutions}

## 附件分析摘要
{attachment_analysis}

---
基于以上材料生成解决方案草稿。
```

---

## 9. 历史工单方案检索

### 新 Collection：`task_resolutions`

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
| `resolved_at` | 解决时间 |

### 检索方式

- `RetrievalService.retrieve_task_resolutions(query, top_k=3)`
- 查询文本：`problem_summary + " " + " ".join(hypotheses) + " " + fault_code + " " + robot_type`

### 回写时机

`/api/ai/task/submit` 中（与状态更新并行，失败不阻塞后端状态变更）。

---

## 10. 与前端的对接约定

### ChatPanel 改造

```diff
// ChatPanel.tsx
- const apiEndpoint = '/api/ai/qa/ask/stream'
+ const apiEndpoint = scene === 'tasks'
+   ? '/api/ai/task/analyze/stream'
+   : '/api/ai/qa/ask/stream'
```

tasks 场景下的消息格式扩展：

```ts
// Message 接口扩展
interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  subtype?: 'solution_draft';      // 任务 Agent 专属
  solution_draft?: SolutionDraft;  // 结构化方案草稿数据
  task_context?: TaskBrief;        // 加载的工单上下文
}
```

### TasksView 改造

1. ChatPanel 对接新 API（上述改动）
2. 方案草稿渲染：`<SolutionCard>` — 根因 + 步骤 + 引用，每条可编辑
3. "提交方案" → `POST /api/ai/task/submit` → 更新本地任务状态

### 前端 API 层（新建文件）

```ts
// src/api/taskAgent.ts（仿 src/api/ai.ts SSE 模式）
export const taskAgentList = (username: string) =>
  aiPost<TaskListResponse>('/task/list', { username });

export const taskAgentAnalyzeStream = (body: TaskAnalyzeRequest, onToken, onResult) =>
  sseStream('/task/analyze/stream', body, onToken, onResult);

export const taskAgentSubmit = (body: TaskSubmitRequest) =>
  aiPost<TaskSubmitResponse>('/task/submit', body);
```

```ts
// src/config/api.ts — 新增
AI_TASK: { BASE_URL: `${API_ROOT}/ai/task` }
```

### 对话持久化

当前 `SceneType` 枚举：`chat / faq / support / consultation / other`。任务 Agent 对话需要新类型 `task_agent`。后端 `call/api/conversation.py` 的 `create_conversation` 需同步支持。

### Workbench store

任务 Agent 新字段（只追加，不新建第三全局 store）：
```ts
// src/stores/workbench.ts — 追加
taskAgentContext?: { taskId: string; title: string }  // 跨视图传递工单上下文
consumeTaskAgentContext: () => TaskAgentContext | null
```

---

## 11. 与提单 Agent 的关系

```
提单 Agent (AiDiagnosisPlatform)        任务 Agent (AiTaskPlatform)
        │                                       │
        │  submit(): 生成工单                    │  analyze(): 分析工单
        │  ├── diagnosis JSON                   │  ├── 读 diagnosis（不复诊）
        │  ├── ticket 入库                      │  ├── 排查树结论检索
        │  └── assigner 派单                    │  ├── 历史方案检索
        │                                       │  ├── 附件解析
        │                                       │  └── 输出 SolutionDraft
        │                                       │
        │  diagnosis JSON ──────────►           │  (数据流转，单向)
        │                                       │
        │                                       │  submit(): 方案确认
        │                                       │  ├── Qdrant ← 方案回写 (AI 侧)
        │                                       │  └── HTTP → 业务后端 → status=resolved
        │                                       │
        └───────────────────────────────────────┘
                        知识库闭环
```

**两个 Agent 永不直接耦合** — 通过 diagnosis JSON 单向传递信息。

---

## 12. 实现计划

### Phase 1: 骨架搭建
- [ ] 创建 `ai/agents/AiTaskPlatform/` 目录
- [ ] `schemas.py` — TaskContext / SolutionDraft / TaskAnalyzeRequest
- [ ] `prompts.py` — system prompt + user prompt 模板
- [ ] `pipeline.py` — AiTaskAgent 核心类 + analyze() 框架
- [ ] `__init__.py` — 导出

### Phase 2: 分析引擎
- [ ] `analyzer.py` — 三路分析编排（排查树结论 + 历史方案 + 附件解析）
- [ ] `attachment_parser.py` — 日志提取 + 回放分析
- [ ] `ai/core/retrieval.py` 新增 `retrieve_task_resolutions()`

### Phase 3: API + 路由
- [ ] `ai/api/router.py` 新增 `task_agent_router` + 全部端点
- [ ] `ai/run.py` 挂载 `task_agent_router`

### Phase 4: 前端对接（需前端同事配合）
- [ ] ChatPanel 切换 API（scene="tasks" → `/api/ai/task/analyze/stream`）
- [ ] 新建 `<SolutionCard>` 可编辑组件
- [ ] TasksView 改造（渲染方案草稿 + 提交按钮）
- [ ] 新建 `src/api/taskAgent.ts`

### Phase 5: 知识库闭环 + 后端对接
- [ ] `submit()` — Qdrant 回写 + 调后端 REST API 更新状态
- [ ] 后端提供 `PUT /api/tasks/{task_id}` 接受 solution 字段
- [ ] 后端 `SceneType` 枚举新增 `task_agent`
- [ ] 批量迁移历史已解决工单到 Qdrant task_resolutions

---

## 13. 团队对接协调清单

> **本节持续维护**。每次发现新的依赖或接口变更，更新本节并通知相关方。

### 13.1 前端侧（需前端工程师配合）

| # | 事项 | 影响范围 | 优先级 | 状态 |
|---|------|---------|:---:|:---:|
| F1 | **ChatPanel SSE 端点切换** — `scene="tasks"` 时调 `/api/ai/task/analyze/stream` | `ChatPanel.tsx` | P1 | 待确认 |
| F2 | **Message 模型扩展** — 当前 `role` 只有 `user | assistant`。需新增字段承载 `solution_draft` 结构化数据，触发 `<SolutionCard>` 渲染（而非纯 Markdown） | `ChatPanel.tsx` Message 接口 | P0 | 待确认 |
| F3 | **`<SolutionCard>` 组件** — 可编辑卡片：根因分析 + 建议步骤（每步可编辑）+ 参考来源 + 置信度 + 提交按钮 | `src/shared/components/SolutionCard.tsx` | P0 | 待实现 |
| F4 | **TasksView 改造** — 方案草稿渲染 + "提交方案" → `POST /api/ai/task/submit` → 更新本地任务状态 | `TasksView.tsx` | P1 | 待确认 |
| F5 | **新建 `src/api/taskAgent.ts`** — 仿 `ai.ts` SSE 模式，导出 `taskAgentList` / `taskAgentAnalyzeStream` / `taskAgentSubmit` | `src/api/taskAgent.ts` | P1 | 待实现 |
| F6 | **`config/api.ts` 新增 AI_TASK 服务** | `src/config/api.ts` | P2 | 待确认 |
| F7 | **Workbench store 追加字段** — 只向 `workbench.ts` 追加，不建新 store | `src/stores/workbench.ts` | P2 | 待确认 |
| F8 | **对话持久化场景类型** — 当前 `SceneType` 无 `task_agent` | 后端 + 前端 | P1 | 待确认 |

### 13.2 后端侧（需后端工程师配合）

| # | 事项 | 影响范围 | 优先级 | 状态 |
|---|------|---------|:---:|:---:|
| B1 | **diagnosis JSON 读取路径** — 当前 `tickets`（有 diagnosis）和 `tasks`（有状态机）是两张独立表，无外键。需确认：通过 `session_id` 关联？还是迁移 diagnosis 到 `tasks.metadata_info`？ | tickets / tasks 表 | P0 | 待与后端确认 |
| B2 | **`PUT /api/tasks/{task_id}` 接受 solution 字段** — 任务 Agent `submit()` 调后端 API 更新状态，不直写 DB。需确认 solution 存哪里（建议 `metadata_info` JSON 字段） | `tasks` 表 / `ticket_service.py` | P0 | 待与后端确认 |
| B3 | **路由前缀无冲突** — `/api/ai/task/*` 在 AI 服务 (8401) 内，与业务后端 `/api/tasks/*` (8400) 不同进程，无贪婪匹配风险 | `ai/run.py` | P0 | ✅ 确认无冲突 |
| B4 | **`GET /api/tasks/{task_id}` 返回 attachments** — 需确认当前响应体包含附件信息供解析 | `task.py` | P1 | 待确认 |
| B5 | **`SceneType` 枚举扩展** — 新增 `task_agent` 类型供对话持久化 | `conversation.py` | P1 | 待确认 |
| B6 | **Ticket/Task 合并进度** — 关注合并计划，目前面向 `tasks` 表开发，diagnosis 暂从 tickets 表读 | 全局 | P2 | 持续关注 |
| B7 | **外部任务源感知** — `TaskContext` 已含 `source` 字段，未来外部源（禅道等）的未映射任务可触发任务 Agent | integrations 层 | P2 | 预留 |

### 13.3 AI 团队内部

| # | 事项 | 优先级 | 状态 |
|---|------|:---:|:---:|
| A1 | **版本管理** — `TASK_AGENT_DESIGN.md` 即权威设计文档，版本号迭代（见顶部 `v0.X`） | P0 | ✅ v0.3 |
| A2 | **检索边界硬编码** — Prompt 中禁止重诊，排查树只取结论节点 | P0 | ✅ §2 + §8 |
| A3 | **`retrieve_task_resolutions()`** — `ai/core/retrieval.py` 新方法 | P0 | 待实现 |
| A4 | **附件解析器** — 日志（正则 ERROR/WARN + 时间线）+ 回放（路径分析），第一期只做日志 | P2 | 待设计 |
| A5 | **`AI_Service_Description.md` 同步** — 重大变更后更新主文档 | P2 | ✅ v1.2 |

### 13.4 不需要担心的

| # | 事项 | 原因 |
|---|------|------|
| N1 | 后端路由注册顺序 | `/api/ai/task/*` 在 AI 服务 (8401) 内，不在业务后端 (8400) |
| N2 | Nginx 配置 | 已有 `deploy/nginx/.../app_gateway.conf` 将 `/api/ai/*` 全部转发到 8401，新路由自动覆盖 |
| N3 | `TaskContext.source` 字段 | 已纳入模型，支持 `manual / zentao / ai_agent` |
