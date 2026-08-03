# OpenRobotService AI 模块说明文档

> 版本：1.3 | 更新日期：2026-07-20

---

## 目录

1. [架构总览](#1-架构总览)
2. [目录结构](#2-目录结构)
3. [AI 基础层 (`core/`)](#3-ai-基础层-core)
4. [智能诊断 Agent (`agents/AiDiagnosisPlatform/`)](#4-智能诊断-agent-agentsaidiagnosisplatform)
5. [任务 Agent (`agents/AiTaskPlatform/`)](#5-任务-agent-agentsaitaskplatform)
6. [数据分析平台 (`agents/AiDataAnalysisPlatform/`)](#6-数据分析平台-agentsaidataanalysisplatform)
6. [API 路由 (`modules/call/api/`)](#6-api-路由-modulescallapi)
8. [知识库入库 (`ingestion/`)](#8-知识库入库-ingestion)
9. [配置系统 (`config.py`)](#9-配置系统-configpy)
10. [启动流程 (`run.py`)](#10-启动流程-runpy)
11. [数据流全景](#11-数据流全景)

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────┐
│                      外部调用层                            │
│   POST /api/ai/qa/ask        POST /api/ai/qa/ask/stream  │
│   POST /api/ai/qa/submit     POST /api/ai/chat            │
│   POST /api/ai/ticketReferee  GET /api/ai/memory/history   │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│                   诊断 Agent 层                            │
│   AiDiagnosisPlatform (pipeline.py)                       │
│   ├── 意图识别 + 知识源选择                                │
│   ├── 5 路并行知识库检索                                   │
│   ├── 故障排查树分流 & 逐步骤引导                          │
│   ├── 多轮对话状态管理 (AgentState)                        │
│   ├── assigner/ 智能派单子模块（自动推荐负责人）            │
│   └── 工单生成 + MySQL 入库 + 自动派单                       │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│                   AI 基础层 (core/)                        │
│   ┌──────────┬──────────┬──────────┬──────────────────┐  │
│   │ LLM      │ Retrieval│ Embed    │ Memory           │  │
│   │ 多厂商   │ 5路并行  │ 本地模型 │ Redis/内存双模   │  │
│   │ 流式+重试│ RRF融合  │ MD5缓存  │ 指代消解         │  │
│   └──────────┴──────────┴──────────┴──────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│                    外部服务层                              │
│   DeepSeek API  │  Qdrant (向量库)  │  Redis (会话)       │
│                 │  5 个独立 collection│                    │
└──────────────────────────────────────────────────────────┘
```

### 设计原则

| 原则 | 说明 |
|------|------|
| **纯 Agent 架构** | 所有用户消息统一走 Agent 推理，无意图路由分支 |
| **知识源不互斥** | FAQ / 车端错误码 / 翻译表 / 排查树 / 操作手册 —— 五路并行检索，按优先级组装 |
| **快速失败** | Qdrant 不可用时 30s 冷却期，不阻塞回复 |
| **双模存储** | Redis 优先，连接失败自动降级到内存 dict |
| **热更新** | Qdrant collection 通过指针文件切换，入库无需重启服务 |
| **隔离启动** | `ai/` 位于项目根目录与 `backend/` 并列，`run.py` 独立启动（端口 8401），绕过 backend 重量级依赖 |

---

## 2. 目录结构

```
ai/                          ← 一级目录，与 backend/、frontend/ 并列
├── __init__.py              # 模块入口：SYSTEM_PROMPT + 公开导出
├── config.py                # 配置模型 AIConfig + 5 个 collection 指针管理
├── exceptions.py            # 9 个自定义异常类
├── run.py                   # FastAPI 独立启动入口（端口 8401，零 backend 依赖）
│
├── api/                     # API 路由（自举，不再依赖 backend）
│   ├── __init__.py          # 导出 qa_router / chat_router / memory_router / assigner_router
│   └── router.py            # 诊断 Agent + LLM 对话 + 会话记忆 + 智能派单 全部端点
│
├── core/                    # AI 基础层
│   ├── __init__.py          # 统一导出（LLM/Retrieval/Embed/Memory）
│   ├── llm.py               # LLM 客户端：多厂商、流式、重试
│   ├── retrieval.py         # 统一检索：5 路独立检索 + RRF 混合
│   ├── embed.py             # Embedding：本地模型加载 + 缓存
│   └── memory.py            # 会话记忆：Redis/内存双模 + 指代消解
│
├── agents/                  # Agent 层
│   ├── AiDiagnosisPlatform/
│   │   ├── pipeline.py      # 诊断 Agent：推理 + 5 路检索 + 工单生成
│   │   └── assigner/        # 智能派单子模块（工单生成后自动推荐负责人）
│   │       ├── assigner.py      # 核心派单逻辑（四层流水线）
│   │       ├── schemas.py       # TicketContext / EngineerProfile / AssignmentResult
│   │       ├── config_loader.py # 配置加载（YAML + prompts.txt）
│   │       ├── config/          # assigner_config.yaml + prompts.txt
│   │       ├── data/            # 结构化参考数据（engineers.json + task_matching.json）
│   │       ├── recall.py        # 多路召回（模块 + 标签 + 历史）
│   │       ├── semantic_recall.py  # 语义召回（Embedding 向量匹配）
│   │       ├── module_inferencer.py # 责任模块推断（LLM + 规则兜底）
│   │       ├── llm_decider.py   # LLM 综合分析决策
│   │       ├── ranker.py        # 精排评分（固定权重多维度）
│   │       ├── decision.py      # 规则决策（阈值判定）
│   │       ├── rule_filter.py   # 规则过滤（层级/负载）
│   │       └── history_matcher.py  # 历史工单匹配
│   ├── AiTaskPlatform/       # 任务 Agent（已实现）
│   │   ├── TASK_AGENT_DESIGN.md # 设计文档
│   │   ├── pipeline.py      # 核心流水线：上下文加载→三路分析→LLM生成
│   │   ├── schemas.py       # TaskContext / SolutionDraft / 请求/响应模型
│   │   ├── prompts.py       # System prompt + user prompt 模板
│   │   ├── analyzer.py      # 三路分析编排：排查树+历史方案+附件
│   │   ├── attachment_parser.py # 日志 ERROR/WARN 提取 + 大文件截断
│   │   └── demo.py          # Mock 数据演示脚本
│   └── AiDataAnalysisPlatform/
│       ├── agent.py         # 门面编排器
│       ├── analyzer.py      # 分析引擎（预处理 + LLM 调用 + 结果解析）
│       ├── config.py        # 多厂商配置（5 家）
│       ├── llm_client.py    # OpenAI SDK 封装
│       ├── prompts.py       # 6 种分析类型提示词模板
│       ├── router.py        # FastAPI 路由
│       └── schemas.py       # Pydantic 数据模型
│
├── ingestion/               # 知识库入库脚本
│   ├── ingest_all.py        # 一键入库编排（5 个知识库）
│   ├── ingest_operation_manual.py  # 操作手册 (Markdown)
│   ├── ingest_faq.py        # FAQ (JSON)
│   ├── ingest_troubleshooting.py   # 故障排查树 (JSON → 线性化)
│   ├── ingest_cheduan.py    # 车端错误码 (PDF 表格解析)
│   └── ingest_translation.py       # USP 翻译表 (XLSX 解析)
│
├── kb/                      # Collection 指针 + Qdrant 本地存储
│   ├── active_collection.txt
│   ├── active_faq_collection.txt
│   ├── active_troubleshooting_collection.txt
│   ├── active_cheduan_collection.txt
│   ├── active_translation_collection.txt
│   └── qdrant/              # Qdrant 本地文件模式数据
│
├── docs/                    # 知识库源文件（operation_doc/faq_doc/cheduan_doc/...）
│
├── embed_models/            # 本地 Embedding 模型缓存
│   └── bge-small-zh-v1.5/
│
├── utils/                   # 工具函数
│   └── keywords.py          # 关键词提取（供召回层使用）
│
└── tests/
    └── agent_chat.py        # 命令行交互测试工具
```

---

## 3. AI 基础层 (`core/`)

### 3.1 LLM 客户端 (`llm.py`)

```
LLMClient
├── 多厂商支持：DeepSeek / OpenAI / Zhipu（Provider 策略模式）
├── complete(prompt, system_prompt, max_tokens, temperature) → str
├── chat(messages, max_tokens, temperature) → str
├── stream(prompt, ...) → AsyncGenerator[str]  （SSE 逐 token）
├── 重试：httpx 网络错误指数退避重试（最多 3 次）
└── 思考模式：检测模型名含 "deepseek"/"mimo" 时自动关闭 thinking
```

**关键参数**（来自 `.env`）：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `DEEPSEEK_API_KEY` | — | API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 地址 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名 |
| `LLM_CONNECT_TIMEOUT` | `3.0` | 连接超时（秒） |
| `LLM_READ_TIMEOUT` | `30.0` | 读取超时（秒） |

### 3.2 Embedding 客户端 (`embed.py`)

```
EmbedClient
├── 本地模型：sentence-transformers (BAAI/bge-small-zh-v1.5)
├── embed(text) → np.ndarray       单条向量化
├── embed_batch(texts) → List[ndarray]  批量向量化
├── 内存缓存：MD5(text) → vector，最多 10000 条
└── 线程池执行：run_in_executor 避免阻塞事件循环
```

### 3.3 检索服务 (`retrieval.py`)

**核心组件**：

| 类/函数 | 职责 |
|---------|------|
| `QdrantClientWrapper` | Qdrant 客户端封装：线程化调用 + 快速失败（30s 冷却） |
| `RetrievalService` | 统一检索入口：5 路独立检索 + 操作手册的 RRF 混合检索 |
| `RetrievalResult` | 检索结果数据类：id, score, title, content, vector_score, images |

**5 路检索方法**：

| 方法 | 目标集合 | 检索方式 | top_k | 置信度检查 |
|------|---------|---------|-------|-----------|
| `retrieve()` | 操作手册 | Dense + Sparse → RRF 融合 | 3 | ✅ 0.65 |
| `retrieve_faq()` | FAQ | 纯 Dense | 2 | ❌ |
| `retrieve_troubleshooting()` | 排查树 | 纯 Dense | 3 | ❌ |
| `retrieve_cheduan()` | 车端错误码 | 纯 Dense | 3 | ❌ |
| `retrieve_translation()` | 翻译表 | 纯 Dense | 2 | ❌ |

**RRF 融合算法**（仅操作手册）：

```
RRF_score(doc) = 1/(k + rank_dense) + 1/(k + rank_sparse)
k = 60（默认）
```

**快速失败机制**：Qdrant 超时或不可用时，标记 `_unavailable = True`，30 秒内所有调用直接返回空列表，不阻塞 Agent 回复。

### 3.4 会话记忆 (`memory.py`)

```
MemoryManager
├── 存储：Redis 优先 → 内存 dict 兜底
├── get_memory(session_id) → SessionMemory
├── add_turn(session_id, role, content) → SessionMemory
├── save_memory(memory) → None
├── resolve_pronoun(query, session_id) → (resolved_query, is_rewritten)
│   ├── 数字选择："2"、"选1"、"第一个" → 结合上文补全
│   ├── 省略追问："然后呢"、"还有呢" → 拼接用户原问题
│   └── 返回拼接后的完整查询，用于提升检索精度
├── add_pending_ticket / remove_pending_ticket / list_pending_tickets
└── 重试间隔：Redis 失败后 60s 内不重试
```

**指代消解模式**（`PRONOUN_PATTERNS`）：

| 正则 | 匹配场景 | 处理方式 |
|------|---------|---------|
| `^\d+$` | 纯数字 | 取上文助手列出的选项，映射到对应项 |
| `选\d+` / `第\d+个` | 选择列表项 | 同上 |
| `然后呢` / `接着` / `下一步` | 省略追问 | 拼接上一条用户消息 + 助手回复要点 |
| `是\d+` / `我选\d+` | 确认列表项 | 同上 |

---

## 4. 智能诊断 Agent (`agents/`)

### 4.1 核心类：`AiDiagnosisPlatform`

```python
class AiDiagnosisPlatform:
    async def run(request: DiagnosisRequest) → dict          # 非流式入口
    async def run_stream(request: DiagnosisRequest) → SSE    # 流式入口
    async def submit(session_id: str) → dict                  # 生成工单 + 写 MySQL + 智能派单
    async def get_ticket(session_id: str) → dict              # 只读获取工单数据（走 LLM，仅历史遗留方法，路由已不调用）
```

### 4.2 请求/状态数据结构

```python
@dataclass
class DiagnosisRequest:
    session_id: str          # 会话 ID（多轮对话关联）
    query: str               # 用户输入
    skip_retrieval: bool     # 测试用：跳过 KB 检索

@dataclass
class AgentState:           # 持久化在 Redis session.metadata 中
    session_id: str
    problem_summary: str     # 问题概述（每轮更新）
    ruled_out: list[str]     # 已排除的原因
    hypotheses: list[str]    # 当前推测
    collected_info: dict     # 已收集的信息（如错误码、版本号）
    diagnosis_rounds: int    # 诊断轮数
    phase: str               # idle | diagnosing | escalated | resolved
    original_query: str      # 用户原始问题
```

### 4.3 Agent 推理流程 (`_agent_think`)

```
用户消息
  │
  ├─ 1. add_turn → Redis 写入用户消息
  ├─ 2. diagnosis_rounds += 1, phase = "diagnosing"
  ├─ 3. resolve_pronoun → 指代消解，补全为完整查询
  │
  ├─ 4. _retrieve_with_context → 5 路并行检索
  │     ├── retrieve (操作手册, RRF 混合)
  │     ├── retrieve_faq
  │     ├── retrieve_troubleshooting
  │     ├── retrieve_cheduan
  │     └── retrieve_translation
  │     结果按优先级组装：FAQ → 车端错误码 → 翻译表 → 排查树 → 操作手册
  │     检索失败 → 返回提示文本："建议稍后重试或转工单"
  │
  ├─ 5. build_prompt → DIAGNOSIS_PROMPT 模板填充
  │     注入：conversation / problem_summary / collected_info /
  │           ruled_out / hypotheses / reference_docs / round
  │
  ├─ 6. LLM complete(prompt, max_tokens=1500, temperature=0.5)
  │     超时 25s，失败返回 "AI 诊断服务暂时不可用"
  │
  ├─ 7. parse_agent_output → 提取 JSON {action, intent, state_update} + 回复文本
  │     支持 4 种格式：fenced JSON, bare JSON, 混合, 纯文本兜底
  │
  ├─ 8. apply_state_update → 更新 AgentState
  ├─ 9. finalize_diagnosis → 单次 Redis save（turn + agent_state）
  │
  └─ 返回 {type, thinking, action, message, agent_state, timing}
```

### 4.4 Knowledge Assembly (知识组装顺序)

检索结果按以下顺序拼入 prompt 的 `reference_docs` 字段：

```
1. FAQ N：{question}         ← 最优先：直接命中时优先采用
   {answer}
2. 🚗 车端错误码 N：{title}  ← 错误码匹配
   {content}
3. 🌐 翻译表 N：{title}      ← 中英文对照
   {content}
4. 🔍 故障排查树 N：{symptom}← 结构化排查引导
   {linearized_tree}
5. 知识库 N（{title}）：      ← 操作手册（基础来源）
   {content + images}
```

### 4.5 Prompt 设计要点

**DIAGNOSIS_PROMPT** 约 170 行，关键约束：

| 规则 | 内容 |
|------|------|
| **知识源不互斥** | 先查 FAQ/车端错误码有无现成答案，有就直接用；都没有的故障才走排查树 |
| **不暴露知识来源** | 禁止说"根据排查树""根据知识库"等话术 |
| **排查树铁律** | 不编造检查项，不跳过步骤顺序，不合并多步，不问树之外的选项 |
| **分流机制** | 命中多个排查树时列出 symptom name 让用户确认，不直接走某一棵 |
| **图片规则** | 每张图必须贴在其所属步骤下，禁止全堆在一个步骤后面 |
| **转工单识别** | 用户说"转工单""转单""生成工单"时直接告知生成，不开始新排查 |
| **防幻觉** | 通用占位符（`<场景A的症状名称>`）替代真实示例，防止 LLM 复述示例内容 |

### 4.6 流式输出 (`_agent_think_stream`)

```
SSE 事件流：
  event: status    → {stage: "retrieving", round: N}     # 检索中
  event: status    → {stage: "analyzing", round: N}      # LLM 推理中
  event: first_token → {ms: 1234}                         # 首个 token 到达
  data: {token: "..."}  × N                               # 逐 token 流式输出
  event: message_created → {message_id: N}               # 后端为 assistant 回复在 call 表建的 DB 消息 id（增量落库接管）
  event: result    → {type, thinking, action, message, ...} # 完整结果
  event: done      → {total_ms: 5678}                     # 结束
```

流式路径在 JSON 结束前静默缓冲（`_find_json_end`），越过 JSON 边界后立即逐 token 透传，前端获得打字机效果。

### 4.7 工单系统

```
submit(session_id)
  ├── LLM 分析对话 + 诊断链 → 工单 JSON
  │     type: problem | bug | feature | support | other
  │     每种 type 有专属字段（如 problem 有 fault_code/robot_type）
  ├── 写入 MySQL (tickets 表)
  ├── agent_state.phase = "resolved"
  ├── add_pending_ticket → Redis Set "usp:pending_tickets"
  ├── assign_ticket() → 智能派单（assigner 一站式入口）
  │     └── 推荐结果注入返回体 (assignee / confidence / reasoning)
  └── 返回 {ticket, db_id, assignee, ...}
```

### 4.8 智能派单 (`assigner/`)

智能派单是诊断 Agent 的**子功能**——工单生成后自动推荐最合适的负责人。它不是独立平台，而是提单 Agent 流程中 `submit()` 的最后一环。

**四层流水线**：

```
TicketContext + EngineerProfile[]
        │
        ▼
【第一层: 规则过滤】RuleFilter
  默认只保留 level=1 的一线工程师，预留负载/可用性过滤扩展
        │
        ▼
【第二层: 多路召回】MultiPathRecaller
  ├── 模块召回：LLM 推断责任模块 → 匹配工程师 responsibility_modules
  ├── 标签召回：关键词提取 → 匹配工程师 skills
  ├── 历史召回：task_matching.json 关键词匹配
  └── 语义召回：Embedding 向量匹配工程师画像 + 历史任务描述
        │
        ▼
【第三层: LLM 综合分析】LlmDecider
  ├── 输入: 工单信息 + 工程师画像 + 各路召回分数
  ├── LLM 直接输出: engineer_id, confidence_score, reasoning, decision_type
  └── 异常时返回 None → 触发回退
        │（回退路径）
        ▼
【第四层: 规则精排 + 决策】Ranker → DecisionMaker
  ├── 固定权重多维度评分（技能30% + 模块30% + 历史25% + 语义15%）
  └── 阈值判定: confidence ≥ 0.8 → auto / ≥ 0.5 → recommend / < 0.5 → fallback
```

**核心类**：

```python
class Assigner:
    """工单负责人推荐器（全异步架构，无依赖注入）"""
    async def aassign(...) -> AssignmentResult

# ── 便捷入口（__init__.py 导出）──
async def assign_ticket(
    title, problem_description, **kwargs
) -> AssignmentResult
    """一站式派单：加载工程师 → 构建 Context → 四层流水线"

def load_engineers() -> list[EngineerProfile]
    """加载工程师画像（模块级缓存）"""


class TicketContext(BaseModel):
    """工单上下文，与提单 Agent 字段完整对齐"""
    id, title, problem_description, status, priority, ticket_type
    session_id, source, location, robot_type, fault_code
    diagnosis_hypotheses, diagnosis_ruled_out, diagnosis_collected_info

class EngineerProfile(BaseModel):
    """工程师画像"""
    id, name, level, responsibility_modules, skills, duty_text

class AssignmentResult(BaseModel):
    """派单结果"""
    engineer_id, engineer_name, confidence_score, reasoning, decision_type
```

**设计原则**：

| 原则 | 说明 |
|------|------|
| **无依赖注入** | LLM / Embedding 直接从 `ai.core` 获取单例，构造函数零参数 |
| **全异步** | 从头到尾用 `ai.core` 异步客户端，不阻塞事件循环 |
| **自动降级** | Embedding 不可用 → 跳过语义召回；LLM 不可用 → 回退到规则精排 |
| **数据本地化** | 工程师画像（`engineers.json`）和任务匹配数据（`task_matching.json`）放在模块内 `data/` 下 |
| **配置热更新** | YAML 配置 + Prompt 模板支持 `reload()`，运行时改规则无需重启 |

**业务接入点**：`pipeline.submit()` 工单写入 MySQL 后调用 Assigner，推荐结果注入 `ticket` 返回体（`assignee / assignee_id / assign_confidence / assign_reasoning`），派单失败不阻塞工单生成。

**LLM 依赖**：

| 调用点 | 用途 | 失败策略 |
|--------|------|---------|
| `ModuleInferencer.ainfer()` | 从工单描述推断责任模块 | 回退到 rule_infer（关键词匹配） |
| `LlmDecider.adecide()` | 综合分析召回分数 + 工程师画像 → 最终推荐 | 回退到 Ranker + DecisionMaker |
| Embedding（`SemanticRecaller`） | 工单描述 vs 工程师画像 / 历史任务的语义相似度 | 跳过语义召回，仅关键词匹配 |


---

## 5. 任务 Agent (`agents/AiTaskPlatform/`)

### 5.1 概述

任务 Agent 是面向**接单工程师**的 AI 助手，对应「系统任务」视角。与提单 Agent（帮客户诊断+转工单）不同，任务 Agent 的目标是基于已有诊断信息 + 知识库 + 历史案例，**生成结构化解决方案草稿，人工校准后提交完成**。

| 维度 | 提单 Agent | 任务 Agent |
|------|-----------|-----------|
| 使用者 | 客户/现场人员 | 接單工程师 |
| 入口 | 我要摇人 | 系统任务 |
| 目标 | 诊断 + 完善信息 + 转工單 | 分析 + 生成方案 + 辅助结单 |
| 知识源 | 5 路 KB 检索 | 5 路 KB + 历史工单方案 (Qdrant) |
| 输出 | 对话式引导 | 结构化 SolutionDraft |

### 5.2 三 Agent 全景

```
                    ai/agents/
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   AiDiagnosisPlatform  AiTaskPlatform  AiDataAnalysisPlatform
   （需求视角）         （供给视角）     （管理视角）
   客户报障+诊断+提单   工程师接单+排查   数据看板+风险分析
        │                    │
        │  diagnosis JSON    │  读 diagnosis
        ├──────────────────►│
        │                    │
        │            ┌───────┘
        │            ▼
        │    生成方案草稿 → 校准 → 提交
        │            │
        └────────────┘  方案回写 Qdrant（闭环）
```

### 5.3 核心流程

1. 工程师选择工单 → 加载上下文（tasks 表 + diagnosis JSON + 对话历史）
2. 多路并行分析：KB 检索 + 历史工单方案检索 + 附件解析（如有）
3. LLM 综合分析 → 生成 SolutionDraft（SSE 流式输出）
4. 前端渲染可编辑草稿 → 工程师校准 → 提交完成
5. 方案回写 Qdrant task_resolutions collection（闭环）

### 5.4 核心数据类

```python
class SolutionDraft(BaseModel):
    root_cause_analysis: str     # 根因分析
    suggested_actions: list[str] # 建议步骤
    references: list[str]        # 参考来源（KB 条目 / 历史工单）
    confidence: float            # 置信度 (0~1)
    needs_more_info: bool        # 是否需要更多信息

class TaskContext(BaseModel):
    task_id, title, description, task_type, priority, status
    problem_summary, hypotheses, ruled_out, collected_info
    fault_code, robot_type, location, attachments, diagnosis_rounds
```

### 5.5 附件解析

| 附件类型 | 解析方式 | 输出 |
|---------|---------|------|
| 日志文件 (txt/log) | 正则提取 ERROR/WARN + 时间线 | 关键事件摘要 |
| 回放文件 | 路径数据提取 (起点/终点/状态变化) | 路径异常报告 |
| 截图 | (暂不做) | — |
| 无附件 | 跳过 | — |

**实现状态**：2026-07-20 已交付。AI 服务端 7 个源文件（pipeline/schemas/prompts/analyzer/attachment_parser/demo）+ 前端集成（ChatPanel 场景切换 + SolutionCard 组件）。Qdrant `task_resolutions` collection 和附件解析的回放支持为后续迭代项。

> 完整设计见 `ai/agents/AiTaskPlatform/TASK_AGENT_DESIGN.md`

## 6. 数据分析平台 (`agents/AiDataAnalysisPlatform/`)

### 5.1 概述

数据分析平台是独立于诊断 Agent 的子系统，专注于**结构化数据的 AI 分析**——将 AGV/AMR 运行数据（故障日志、任务记录、风险项等）输入大模型，产出结构化的分析报告。

与诊断 Agent 的关键区别：

| 维度 | 诊断 Agent | 数据分析平台 |
|------|-----------|-------------|
| 输入 | 用户自然语言提问 | 结构化数据（JSON/CSV/文本） |
| 知识来源 | 5 路知识库检索 | 用户直接提供的数据 |
| 输出 | 对话式回复 + 工单 | 结构化分析报告（摘要+洞察+建议） |
| LLM 客户端 | `core/llm.py`（httpx） | 独立 `LLMClient`（openai SDK） |
| 厂商支持 | DeepSeek / OpenAI | DeepSeek / Qwen / GLM / SiliconFlow / OpenAI兼容 |

### 5.2 目录结构

```
agents/AiDataAnalysisPlatform/
├── __init__.py      # 导出 agent/analyzer/config/llm_client/router/schemas
├── agent.py         # DataAnalysisAgent — 门面编排器
├── analyzer.py      # DataAnalyzer — 分析引擎（预处理+LLM调用+结果解析）
├── config.py        # AnalysisConfig — 多厂商配置（5家）
├── llm_client.py    # LLMClient — OpenAI SDK 封装
├── prompts.py       # 系统提示词模板（6种分析类型）
├── router.py        # FastAPI 路由
└── schemas.py       # Pydantic 数据模型
```

### 5.3 核心类：`DataAnalysisAgent`

门面模式——对外暴露统一入口，内部委托给 `DataAnalyzer`：

```python
class DataAnalysisAgent:
    # 工厂方法
    @classmethod def from_env() -> DataAnalysisAgent

    # 核心能力
    async def analyze(data, data_source, analysis_type, question, context) -> AnalysisResult
    async def analyze_stream(data, ...) -> AsyncIterator[str]   # SSE 流式
    async def chat(question, context) -> ChatResponse           # 纯文本对话

    # 运维
    def health_check() -> HealthResponse
```

### 5.4 分析引擎：`DataAnalyzer`

```
analyze() 流程：
  1. preprocess_data() → 数据预处理
     ├── JSON  → json.dumps(indent=2) 美化
     ├── CSV   → pd.read_csv → tabulate 转 Markdown 表格
     └── 其他  → 原样透传

  2. build_system_prompt(analysis_type) → 按分析类型选模板
  3. build_user_prompt(data, question, context) → 组装用户消息
  4. llm_client.chat(system, user) → 调用大模型
  5. _parse_result() → 正则提取结构化字段
     ├── _extract_summary()       → **摘要** 或首段
     ├── _extract_insights()      → ### 标题块，自动判定 severity
     └── _extract_recommendations() → 行动建议中的有序列表
```

### 5.5 分析类型（`AnalysisType`）

| 枚举值 | 说明 | Prompt 注入的关键分析维度 |
|--------|------|--------------------------|
| `general` | 通用分析 | 数据概览、趋势、异常值、关键发现 |
| `fault` | 故障分析 | 故障分布、故障趋势、高频故障 TOP5、严重故障预警 |
| `task_stats` | 任务统计 | 任务总量/成功率、效率指标、低效机器人识别 |
| `risk` | 风险评估 | 风险级别分布、高风险项、逾期风险、工作负载 |
| `trend` | 趋势预测 | 历史趋势、短期预测、潜在风险预警 |
| `custom` | 自定义 | 按用户问题自由分析 |

### 5.6 多厂商支持

通过 OpenAI 兼容 API 统一对接，切换只需改环境变量：

| 厂商 | `LLM_PROVIDER` | API Key 环境变量 | 默认模型 |
|------|---------------|-----------------|---------|
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| 通义千问 | `qwen` | `QWEN_API_KEY` | `qwen-plus` |
| 智谱 GLM | `glm` | `GLM_API_KEY` | `glm-4-flash` |
| 硅基流动 | `siliconflow` | `SILICONFLOW_API_KEY` | `Qwen/Qwen2.5-7B-Instruct` |
| OpenAI 兼容 | `openai_compatible` | `LLM_API_KEY` | 自定义 |

```bash
# .env 示例：切换到通义千问
LLM_PROVIDER=qwen
QWEN_API_KEY=sk-xxx
QWEN_MODEL=qwen-plus
```

### 5.7 输出格式

LLM 被要求按 Markdown 模板输出，`DataAnalyzer` 再解析为结构化对象：

```python
class AnalysisResult:
    analysis_type: AnalysisType        # 分析类型
    summary: str                       # 一句话摘要
    insights: list[AnalysisInsight]    # 关键洞察（category + content + severity）
    recommendations: list[str]         # 行动建议
    raw_response: str | None           # LLM 原始回复
    model: str | None                  # 使用的模型名
    usage: dict | None                 # token 用量
```

### 5.8 API 路由（`/api/ai/analysis`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ai/analysis/health` | 健康检查（返回 provider/model/base_url） |
| POST | `/api/ai/analysis/analyze` | 数据分析（`stream=false` 返回结构化结果，`stream=true` 返回 SSE） |
| POST | `/api/ai/analysis/chat` | 快速对话（无数据的纯文本问答） |
| GET | `/api/ai/analysis/types` | 支持的分析类型和数据格式枚举 |

**请求示例**：

```json
POST /api/ai/analysis/analyze
{
    "data": "[{\"robot_id\": \"R001\", \"fault_code\": \"E1601\", \"level\": \"Error\"}]",
    "data_source": "json",
    "analysis_type": "fault",
    "question": "哪些故障最频繁？"
}
```

**响应示例**：

```json
{
    "analysis_type": "fault",
    "summary": "当前时段共发生 12 次故障，其中通讯类故障占比最高（58%），需重点关注",
    "insights": [
        {"category": "故障分布统计", "content": "通讯类故障 7 次、任务类 3 次...", "severity": "warning"}
    ],
    "recommendations": ["检查调度服务器网络稳定性", "排查 E1601 相关配置"],
    "model": "deepseek-chat",
    "usage": {"total_tokens": 1234}
}
```

### 5.9 与诊断 Agent 的关系

两个 Agent 完全独立，互不依赖：
- **诊断 Agent** 使用 `core/llm.py`（httpx + tenacity 重试），面向终端用户的多轮对话
- **数据分析平台** 使用独立的 `openai.AsyncOpenAI` SDK，面向批量数据分析
- 两者的 Router 分别在 `run.py` 中独立挂载，可以单独启用/停用

---

## 8. API 路由 (`api/`)

### 8.1 诊断 Agent (`/api/ai/qa`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/qa/ask` | 非流式问答（含诊断追问与提单） |
| POST | `/api/ai/qa/ask/stream` | SSE 流式问答 |
| POST | `/api/ai/qa/submit` | 生成工单并存库 |
| GET | `/api/ai/qa/ticket?session_id=` | 获取工单数据（已提交工单走 DB 快照，不跑 LLM） |
| POST | `/api/ai/qa/ticket/ack` | 派单确认回执 |
| POST | `/api/ai/qa/upload` | 上传附件 |
| GET | `/api/ai/qa/health` | 健康检查 |

**`/api/ai/qa/upload`（multipart/form-data）字段**：`session_id`（必填）、`files`（必填，`File[]`）、`message`（可选，附带文字）。响应 `data` 字段：`saved`/`files`（`{filename,size,path}`）、`ack_message`（后端确认回执：只传图片=VLM 初步诊断；只传非图片=「暂不支持解析除图片以外的文件类型，提单后将作为参考」）、`ai_response`（仅 `message` 非空时有值：`{message, action, thinking, ticket}`，含完整诊断回复，若触发提单带 `ticket`）。文件名不注入对话上下文（后端只写 assistant turn，避免文件名数字误导 LLM）。

**请求示例**：

```json
POST /api/ai/qa/ask
{
    "session_id": "abc123",
    "query": "车子不动了，任务状态显示调度中"
}
```

**响应示例**：

```json
{
    "code": 0,
    "type": "diagnosis",
    "action": "ask",
    "message": "🔍 排查树匹配到以下几种情况，请确认你的具体现象：\n1. 车待命，任务状态一直显示调度中，机器人编号为-\n...",
    "agent_state": {
        "phase": "diagnosing",
        "problem_summary": "车辆调度中无法执行任务",
        "diagnosis_rounds": 1,
        "hypotheses": ["调度任务下发异常"],
        "collected_fields": []
    }
}
```

**`/api/ai/qa/ask/stream`（后端增量落库）**：请求体新增可选 `conversation_id`（前端已落库的 `call` 会话 id）与 `assistant_message_id`（已预建消息 id）。传入 `conversation_id` 时，后端在流式中把 assistant 回复**增量写同会话 messages 表**（`PERSIST_MS=0.8s`）：首 token 创建消息、回传 `event: message_created → {message_id}`、流结束/异常 `force` 落库最终内容。前端刷新/切会话即可从 DB 恢复（最多丢最后 <0.8s），不再依赖前端内存。`conversation_id` 为空或建消息失败则降级不持久化，由前端兜底。

### 8.2 LLM 对话 (`/api/ai/chat`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/chat` | 非流式纯 LLM 对话 |
| POST | `/api/ai/chat/stream` | SSE 流式纯 LLM 对话 |

### 8.3 会话记忆 (`/api/ai/memory`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ai/memory/history?session_id=` | 查看对话历史 |
| GET | `/api/ai/memory/tickets` | 待派单列表 |
| DELETE | `/api/ai/memory/clear?session_id=` | 清除指定会话 |
| DELETE | `/api/ai/memory/clear-all` | 清除所有会话 |

### 8.4 智能派单 (`/api/ai/ticketReferee`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/ticketReferee` | 智能派单（输入 title + comments → 推荐最佳工程师） |
| GET | `/api/ai/ticketReferee/health` | 派单服务健康检查 |

**请求示例**：

```json
POST /api/ai/ticketReferee
{
    "title": "AGV小车无法启动",
    "comments": ["潜伏车上线后无法移动", "MQTT连接正常但调度系统下发任务无响应"],
    "workload_map": {"张三": 5}
}
```

**响应示例**：

```json
{
    "code": 200,
    "data": {
        "name": "张资源",
        "id": "eng_zhangzhiy",
        "confidence": 0.85,
        "decision_type": "auto",
        "reasoning": "推荐 张资源，综合置信度 0.85。依据: 责任模块匹配(0.50): 车端, 异常排查；语义相似度(0.53)。匹配度高，可直接派单。"
    }
}
```

---

## 9. 知识库入库 (`ingestion/`)

### 8.5 任务 Agent (`/api/ai/task`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/task/list` | 列出当前用户待处理工单 (Agent 视角) |
| POST | `/api/ai/task/analyze` | 分析工单 → 生成方案草稿 |
| POST | `/api/ai/task/analyze/stream` | SSE 流式分析 |
| POST | `/api/ai/task/submit` | 确认方案 → 保存 + 回写知识库 |
| GET | `/api/ai/task/health` | 健康检查 |

### 9.1 统一入口：`ingest_all.py`

```bash
# 一键入库全部 5 个知识库
python -m ai.ingestion.ingest_all

# 跳过指定知识库
python -m ai.ingestion.ingest_all --skip faq --skip translation

# 预览（仅排查树支持）
python -m ai.ingestion.ingest_all --dry-run
```

注册表 (`_REGISTRY`)：

```python
[
    ("ingest_operation_manual",  "操作手册"),
    ("ingest_faq",               "FAQ"),
    ("ingest_troubleshooting",   "问题排查树"),
    ("ingest_cheduan",           "车端错误码"),
    ("ingest_translation",       "翻译表"),
]
```

### 9.2 各知识库详情

| 知识库 | 脚本 | 数据源 | 解析方式 | Chunk 策略 | Collection 前缀 |
|--------|------|--------|---------|-----------|----------------|
| 操作手册 | `ingest_operation_manual.py` | `docs/operation_doc/*.md` | Markdown 分段 | 按 `##` 标题切分 + 图片关联 | `operation_docs` |
| FAQ | `ingest_faq.py` | `docs/faq_doc/*.json` | JSON 解析 | 每个 Q&A 对为一个 chunk | `faq_docs` |
| 排查树 | `ingest_troubleshooting.py` | `docs/问题排查树_v1.json` | JSON → 树线性化 | 每个 symptom (46 个) 为一个 chunk | `troubleshooting` |
| 车端错误码 | `ingest_cheduan.py` | `docs/cheduan_doc/*.pdf` | pdfplumber 表解析 + 文本兜底 | 每个错误码 (55 条) 为一个 chunk | `cheduan` |
| 翻译表 | `ingest_translation.py` | `docs/translation_doc/*.xlsx` | zipfile + xml.etree | 按 namespace 分组 (5 个 chunk) | `translation` |

### 9.3 Collection 热更新机制

```
入库流程：
  1. 解析文档 → chunk 列表
  2. 向量化 → 写入新 collection: {prefix}_{timestamp}
  3. 写指针文件: ai/kb/active_{prefix}_collection.txt
  4. cleanup: 保留最新 2 个 collection，删除旧版

热更新：
  RetrievalService 每次检索时实时读取指针文件
  → 自动指向新 collection，服务无需重启
```

### 9.4 排查树线性化格式

原始 JSON 树结构 → 可读文本：

```
{车不动了，原子任务状态路径规划中}

第1步：请确认路径规划界面是否有红色报错
  → 用户说有 → 【结论】原因：路径被障碍物堵塞。方案：清除障碍后重试
  → 用户说没有 → 进入第2步

第2步：请检查车辆定位状态是否为"已定位"
  → 用户说是 → 【结论】原因：...
  → 用户说不是 → 进入第3步
```

---

## 10. 配置系统 (`config.py`)

### 8.1 配置模型 `AIConfig`

所有配置从 `.env` 注入，运行时通过 `get_ai_config()` 获取（`@lru_cache` 单例）。

```python
class AIConfig(BaseModel):
    # LLM
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    llm_connect_timeout: float = 3.0
    llm_read_timeout: float = 30.0

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_timeout: float = 5.0
    qdrant_local_path: str = ""       # 非空时启用本地文件模式

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_max_context_turns: int = 3
    redis_ttl: int = 0                 # 0=永久

    # Embedding
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32
    embedding_cache_size: int = 10000

    # 检索
    retrieval_top_k: int = 3
    retrieval_score_threshold: float = 0.65

    # 派单
    dispatch_api_url: str = ""
    upload_dir: str = "./uploads"

    # 文档路径
    docs_path: str = ""                # 原始文档根目录
    media_url_prefix: str = "/api/media"
```

### 8.2 Collection 指针管理

```python
get_active_collection()          → 操作手册 collection 名
get_active_faq_collection()      → FAQ collection 名
get_active_troubleshooting_collection() → 排查树 collection 名
get_active_cheduan_collection()         → 车端错误码 collection 名
get_active_translation_collection()     → 翻译表 collection 名
```

每个函数从 `ai/kb/active_*_collection.txt` 读取，文件不存在时返回空字符串（检索时静默跳过）。

### 8.3 启动连通性检查 (`validate_ai_config`)

```python
async def validate_ai_config() → dict:
    # 返回 {"deepseek": {status, message}, "qdrant": {status, message}, "redis": {status, message}}
```

---

## 11. 启动流程 (`run.py`)

### 9.1 零依赖启动

`ai/run.py` 完全自举，不依赖 backend 任何模块。仅需项目根目录在 `sys.path` 中以使 `ai.*` 可导入，配置统一从 `ai/.env` 读取。路由从 `ai.api` 直接挂载，无需 namespace hack。

### 9.2 Lifespan 启动序列

```
FastAPI lifespan:
  ├── 1. validate_ai_config → 连通性检查 (DeepSeek + Qdrant + Redis)
  ├── 2. Embedding 模型预加载 (pre-warm)
  ├── 3. Qdrant collection 自动检查 & 入库：
  │     ├── 操作手册 未就绪 → auto_ingest()
  │     ├── FAQ       未就绪 → auto_ingest()
  │     ├── 排查树    未就绪 → auto_ingest()
  │     ├── 车端错误码 未就绪 → auto_ingest()
  │     └── 翻译表    未就绪 → auto_ingest()
  └── 4. 挂载静态资源 (media/)
```

### 9.3 启动命令

```bash
cd OpenRobotService
python ai/run.py    # 默认端口 8401
```

---

## 12. 数据流全景

### 12.1 用户提问 → Agent 回复

```
POST /api/ai/qa/ask {session_id, query}
        │
        ▼
┌──────────────────────────┐
│ AiDiagnosisPlatform.run   │
│                          │
│ 1. get_memory(session_id)│  ← Redis/内存
│ 2. add_turn(user, query) │  → Redis/内存
│ 3. resolve_pronoun       │  → 结合历史补全省略表达
│                          │
│ 4. ═══ 5 路并行检索 ═══  │
│    ├── retrieve          │  ← Qdrant (操作手册)
│    ├── retrieve_faq      │  ← Qdrant (FAQ)
│    ├── retrieve_trbl     │  ← Qdrant (排查树)
│    ├── retrieve_cheduan  │  ← Qdrant (车端错误码)
│    └── retrieve_trns     │  ← Qdrant (翻译表)
│    结果按优先级组装 docs   │
│                          │
│ 5. build_prompt          │  → 填充 DIAGNOSIS_PROMPT
│ 6. LLM.complete          │  ← DeepSeek API (25s 超时)
│ 7. parse_agent_output    │  → {action, state_update, message}
│ 8. apply_state_update    │  → 更新 AgentState
│ 9. save_memory           │  → Redis/内存（单次保存）
│                          │
│ 返回 {message, action,   │
│        agent_state,      │
│        timing}            │
└──────────────────────────┘
```

### 12.2 生成工单

```
POST /api/ai/qa/submit {session_id}
        │
        ▼
┌──────────────────────────┐
│ AiDiagnosisPlatform.submit│
│                          │
│ 1. get_memory            │
│ 2. _build_ticket:        │
│    ├── 格式化对话记录     │
│    ├── LLM 分析生成 JSON  │  ← 工单结构化字段
│    └── 填充专属字段       │
│ 3. MySQL INSERT          │  → tickets 表
│ 4. add_pending_ticket    │  → Redis Set
│ 5. assigner.aassign()    │  → 智能派单推荐负责人（四层流水线）
│    ├── 规则过滤          │
│    ├── 多路召回（模块+标签+历史+语义）
│    ├── LLM 综合分析      │
│    └── 规则精排（回退）  │
│ 6. save_memory           │
│                          │
│ 返回 {ticket, db_id,     │
│        assignee, ...}    │
└──────────────────────────┘
```

### 12.3 流式 SSE

```
POST /api/ai/qa/ask/stream
        │
        ▼
┌──────────────────────────┐
│ run_stream               │
│                          │
│ event: status            │  "retrieving"
│ ... 检索（同非流式）...   │
│ event: status            │  "analyzing"
│                          │
│ LLM.stream               │
│   ├── 缓冲 JSON 部分      │  _find_json_end 定位边界
│   └── token 透传          │  event: token × N
│                          │
│ event: first_token       │  {ms: 1234}
│ event: message_created   │  {message_id: N}   # 后端为 assistant 回复建的 DB 消息 id（增量落库）
│ event: result            │  完整结果 JSON
│ event: done              │  {total_ms: 5678}
└──────────────────────────┘
```

---

### 12.4 任务 Agent — 工单分析

```
POST /api/ai/task/analyze/stream {task_id, session_id}
        │
        ▼
┌──────────────────────────┐
│ AiTaskAgent.analyze()     │
│                          │
│ 1. 加载工单上下文         │
│    ├── GET /api/tasks/   │  ← 业务后端 REST
│    └── diagnosis JSON    │  ← tickets 表 / Redis
│                          │
│ 2. 多路分析（并行）       │
│    ├── KB 检索           │  ← Qdrant (5 collections)
│    ├── 历史方案检索       │  ← Qdrant (task_resolutions)
│    └── 附件解析 (如有)   │  ← attachment_parser
│                          │
│ 3. build_prompt          │
│ 4. LLM.stream            │  ← DeepSeek API
│ 5. parse → SolutionDraft │
│ 6. save to memory        │
│                          │
│ 返回 SSE → 前端渲染       │
└──────────────────────────┘
```

## 附录 A：异常类体系

```
AIError (base)
├── AITimeoutError          LLM 调用 / 外部服务超时
├── RetrieveEmptyError      检索结果为空
├── LowConfidenceError      检索置信度低于阈值
├── IntentNotMatchError     意图非操作类（保留，当前 Agent 模式较少触发）
├── JSONParseError          LLM 输出 JSON 解析失败
├── ContextRewriteError     指代消解失败
├── EmbeddingError          Embedding 模型/向量化失败
└── ServiceUnavailableError 外部服务不可用（带 service_name 标识）
```

## 附录 B：全局单例汇总

| 单例 | 获取函数 | 说明 |
|------|---------|------|
| `AIConfig` | `get_ai_config()` | 配置（lru_cache 内存单例） |
| `LLMClient` | `get_llm_client()` | LLM 客户端 |
| `EmbedClient` | `get_embed_client()` | Embedding 客户端 |
| `RetrievalService` | `get_retrieval_service()` | 检索服务 |
| `MemoryManager` | `get_memory_manager()` | 会话记忆管理 |
| `AiDiagnosisPlatform` | `get_diagnosis_platform()` | 诊断 Agent（提单） |
| `AiTaskAgent` | `get_task_agent()` | 任务 Agent（方案生成） |

## 附录 C：环境变量速查

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | — | **必填** |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | LLM 模型 |
| `QDRANT_HOST` | `localhost` | 向量库地址 |
| `QDRANT_PORT` | `6333` | 向量库端口 |
| `QDRANT_LOCAL_PATH` | `""` | 本地文件模式 |
| `QDRANT_TIMEOUT` | `5.0` | 向量库超时 |
| `REDIS_URL` | `redis://localhost:6379/0` | 会话存储 |
| `REDIS_TTL` | `0` | 会话过期（0=永久） |
| `EMBEDDING_MODEL_NAME` | `BAAI/bge-small-zh-v1.5` | Embedding 模型 |
| `EMBEDDING_DEVICE` | `cpu` | 推理设备 |
| `RETRIEVAL_TOP_K` | `3` | 检索返回数量 |
| `RETRIEVAL_SCORE_THRESHOLD` | `0.65` | 置信度阈值 |
| `DOCS_PATH` | `""` | 文档根目录（默认 ai/docs/） |
| `MEDIA_URL_PREFIX` | `/api/media` | 图片 URL 前缀 |
| `AI_CHAIN_TIMEOUT` | `2.5` | (保留字段) |
| `DISPATCH_API_URL` | `""` | 派单系统回调地址 |
| `UPLOAD_DIR` | `./uploads` | 附件上传目录 |
