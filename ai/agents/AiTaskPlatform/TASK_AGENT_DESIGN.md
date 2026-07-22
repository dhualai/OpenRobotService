# AiTaskPlatform — 任务 Agent 设计文档

> 版本：3.0 | 日期：2026-07-22
>
> **本文件是 AiTaskPlatform 的权威设计文档**，供开发时参考和每次新对话恢复上下文。
>
> **当前状态**：v3.0 架构重构——砍掉大聊天，任务 Agent 聚焦工单详情页。能力拆分为 Skill/Tool，@AI 讨论 + 诊断报告 + 讨论摘要。

---

## 更新日志

| 日期 | 版本 | 变更摘要 |
|------|:---:|------|
| 2026-07-22 | 3.0 | **架构重构**：砍掉大聊天；Agent 聚焦工单详情页；新增 诊断报告 + @AI 讨论 + 讨论摘要。诊断报告为即时生成不落库。 |
| 2026-07-21 | 2.2 | Day 2 收尾：ZIP/文件夹解析；Chat v2.0；diagnosis service |
| 2026-07-21 | 2.0 | Chat + Diagnosis 分离 |
| 2026-07-20 | 1.0 | 初始交付 |

---

## 目录

1. [v3.0 架构总览](#1-v30-架构总览)
2. [交互流程](#2-交互流程)
3. [API 契约](#3-api-契约)
4. [Skill/Tool 能力体系](#4-skilltool-能力体系)
5. [诊断报告](#5-诊断报告)
6. [@AI 讨论回复](#6-ai-讨论回复)
7. [讨论摘要](#7-讨论摘要)
8. [与前端的数据契约](#8-与前端的数据契约)
9. [v2.x → v3.0 变更清单](#9-v2x--v30-变更清单)
10. [实现计划](#10-实现计划)

---

## 1. v3.0 架构总览

### 核心变化

```
v2.x（旧）:
  系统任务页面
  ├── ChatPanel（自由聊天，感知全量工单）         ← 砍掉
  └── 工单详情（AI诊断在评论里）

v3.0（新）:
  系统任务页面
  ├── 工单卡片列表（无 ChatPanel）
  │
  └── 点击进入 → 工单详情页（独立页面，和「我要摇人」分开）
       ├── 工单信息区
       ├── 讨论区（含 @AI 能力）
       │    └── [@AI 帮我看看] 按钮 / 输入框
       ├── [帮我分析] 按钮 → 触发诊断报告
       └── 讨论摘要卡片（AI 自动生成）
```

### 三个核心能力

| 能力 | 触发方式 | 说明 |
|------|------|------|
| **@AI 讨论** | 前端输入框 + @ 按钮 | 任务 Agent 基于讨论历史 + 工单上下文回复 |
| **诊断报告** | [帮我分析] 按钮 | 调用全能力（日志解析 / 图片分析 / 排查树 / 历史方案）→ 输出结构化报告 |
| **讨论摘要** | AI 后台定时扫描 | 检测讨论更新 → 总结新讨论的摘要 → 写 task_comments |

### 与前端完全解耦

前端只管调 API。我们暴露 4 个端点，前端同事按契约接入。

---

## 2. 交互流程

### 2.1 工程师进入工单详情页

```
系统任务 → 点击工单卡片 → 进入独立工单详情页

页面布局:
  ┌──────────────────────────────────────────┐
  │  工单 #44946  避让后车不动                 │
  │  状态: 进行中  |  优先级: 高  |  潜伏车     │
  │  描述: 44946避让生成的时候...              │
  │                                            │
  │  [帮我分析]  按钮                          │
  │                                            │
  │  ── 讨论区 ────────────────────────────   │
  │  👤 张工: 日志拿到了，帮我看看             │
  │  🤖 @AI: 根据日志，时间是14:40...         │
  │  👤 张工: 找到问题了，MAPF版本太旧         │
  │                                            │
  │  [输入框] [@AI] [发送]                    │
  │                                            │
  │  ── 讨论摘要 ──────────────────────────   │
  │  📝 2026-07-22 15:30                       │
  │  张工确认根因为MAPF v1.1.2版本缺陷...      │
  └──────────────────────────────────────────┘
```

### 2.2 工程师点击 [帮我分析]

```
POST /api/ai/task/diagnose { task_id }
  │
  ├─ 1. 加载工单上下文
  │     └── task_adapter.load_task_context_dict(task_id)
  │           → title, description, diagnosis, attachments
  │
  ├─ 2. 扫描可用附件 → 激活对应 Skill
  │     ├── 有日志文件 → LogParser
  │     ├── 有图片 → ImageAnalyzer (暂标记，后续OCR)
  │     ├── 有 ZIP → ZipExtractor → LogParser
  │     └── 无附件 → 跳过
  │
  ├─ 3. 知识库检索（Skill: KnowledgeRetriever）
  │     ├── 排查树结论节点
  │     └── 历史工单方案 (task_resolutions)
  │
  ├─ 4. LLM 综合分析 → 输出诊断报告
  │     └── report: { root_cause, steps, references, confidence }
  │
  ├─ 5. 诊断报告写入 task_comments（AI任务助手）
  │
  └─ 返回报告 JSON + task_comments 中自动展示
```

### 2.3 工程师 @AI 提问

```
POST /api/ai/task/discuss { task_id, query?, context_discussion }
  │
  ├─ 1. 加载讨论历史（最近 10 条 task_comments）
  │
  ├─ 2. 加载工单上下文
  │
  ├─ 3. LLM 基于讨论+上下文回复
  │     └── 如果 query 为空 → 基于讨论内容主动给出分析
  │     └── 如果 query 有值 → 针对具体问题回答
  │
  └─ 4. 回复写入 task_comments（AI任务助手）
```

---

## 3. API 契约

### 端点一览（v3.0）

| 方法 | 路径 | 说明 | 触发 |
|------|------|------|------|
| POST | `/api/ai/task/diagnose` | 全能力诊断 → 即时返回报告（不落库） | [帮我分析] 按钮 |
| POST | `/api/ai/task/discuss` | @AI 讨论回复（带讨论上下文）→ 写 task_comments | 讨论区 @AI |
| POST | `/api/ai/task/summarize` | 检测新讨论 → 生成摘要 → 写 task_comments | 后台定时 / 手动 |
| POST | `/api/ai/task/submit` | 提交方案 → 更新工单 + Qdrant 回写 | 工程师确认 |
| GET | `/api/ai/task/health` | 健康检查 | 运维 |

### 废弃的端点（v2.x → 移除）

| 端点 | 原因 |
|------|------|
| `POST /api/ai/task/chat/stream` | v3.0 取消大聊天 |
| `POST /api/ai/task/chat` | 同上 |
| `POST /api/ai/task/analyze/stream` | 合并到 `/diagnose` |
| `POST /api/ai/task/analyze` | 同上 |
| `POST /api/ai/task/list` | 前端走业务后端 API |

### 关键请求/响应

#### POST /api/ai/task/diagnose

即时生成诊断报告，**不存数据库**。前端拿到 JSON 后直接渲染弹窗。

```json
// Request
{ "task_id": "44946" }

// Response
{
  "code": 0,
  "data": {
    "task_id": "44946",
    "root_cause_analysis": "MAPF v1.1.2 避让算法在特定场景下...",
    "suggested_actions": [
      "1. 回退 MAPF 版本至 v1.1.1",
      "2. 设置 avoidance_distance_threshold >= 2.0m"
    ],
    "evidence": [
      "🔍 排查树「车不动」结论：路径死循环",
      "📋 历史工单 #44123：同版本同症状",
      "📄 日志: 6行, 4条异常, 14:40:02~14:40:05"
    ],
    "capabilities_used": ["log_parser", "knowledge_retrieval"],
    "confidence": 0.85
  }
}
```

#### POST /api/ai/task/discuss

```json
// Request
{
  "task_id": "44946",
  "query": "日志文件分析有什么需要注意的地方？",
  "context": {
    "recent_comments": [
      {"author": "张工", "content": "日志拿到了，帮我看看", "created_at": "..."},
      {"author": "AI任务助手", "content": "请提供日志内容", "created_at": "..."}
    ]
  }
}

// Response
{
  "code": 0,
  "data": {
    "task_id": "44946",
    "reply": "分析这份日志时需要注意：1. 关注14:40前后的ERROR行...",
    "comment_id": 123
  }
}
```

#### POST /api/ai/task/summarize

```json
// Request
{ "task_id": "44946" }

// Response
{
  "code": 0,
  "data": {
    "task_id": "44946",
    "summary": "张工确认根因为MAPF v1.1.2版本缺陷，已回退至v1.1.1解决。建议提issue给算法组。",
    "new_messages_since_last_summary": 5,
    "comment_id": 124
  }
}
```

---

## 4. 能力体系

三个核心功能各自依赖的能力不同。每项能力下包含具体的实现函数，按需调用。

```
                         ┌─────────────────────┐
                         │  工单上下文加载       │  ← 公共依赖，每个功能都用
                         │  load_task_context() │
                         └─────────┬───────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
  ┌──────────┐             ┌──────────────┐            ┌──────────────┐
  │ 诊断报告  │             │  @AI 讨论     │            │  讨论摘要     │
  │ /diagnose│             │  /discuss     │            │  /summarize  │
  └────┬─────┘             └──────┬───────┘            └──────┬───────┘
       │                          │                           │
       ▼                          ▼                           ▼
  ┌──────────┐             ┌──────────────┐            ┌──────────────┐
  │ ✅ 附件分析│              │ ✅ 讨论区理解  │            │ ✅ 讨论区理解  │
  │ ✅ 知识库 │              │               │            │               │
  └──────────┘             └──────────────┘            └──────────────┘
```

---

### 4.1 公共依赖：工单上下文加载

三个功能都需要。从 `task_adapter.load_task_context_dict(task_id)` 获取工单全量信息（title/description/diagnosis/attachments/robot_type/fault_code/status/priority），不是独立能力，是一个公共函数。

---

### 4.2 能力一：附件分析

**用途**：诊断报告生成时，分析工单附带的文件。@AI 讨论和讨论摘要不需要。

**分类依据**：当前附件处理的"深度"还不够——日志也就是提取 ERROR 行，图片也就是列出文件名，ZIP 就是解压遍历。它们本质上都是"读附件 → 提取文本摘要"，往后继续各自升级（比如图片走 OCR）才值得拆开。现阶段统一为附件分析，用一个入口函数对附件列表做分派。

| 函数 | 说明 | 触发条件 | 状态 |
|------|------|------|:---:|
| `analyze_attachments(attachments)` | 入口：遍历附件列表 → 按类型分派 | 有附件时自动调用 | ✅ |
| └ `_extract_log_text(file)` | 日志/文本：ERROR/WARN 提取 + 时间线 | 附件为 .txt/.log/.csv | ✅ 已有 |
| └ `_extract_zip_text(file)` | ZIP：内存解压 → 识别内部日志文件 | 附件为 .zip | ✅ 已有 |
| └ `_traverse_dir_text(path)` | 文件夹：遍历目录树 → 识别日志文件 | 路径为本地目录 | ✅ 已有 |
| └ `_list_image_info(files)` | 图片：提取文件名列表（暂不做 OCR） | 附件为 .jpg/.png 等 | ✅ 已有 |

---

### 4.3 能力二：历史工单检索

**用途**：诊断报告生成时，用 diagnosis JSON 中的信息检索相似已解决工单的方案。@AI 讨论和讨论摘要不需要。

**为什么不含排查树**：提单 Agent 在初次诊断时已经走了排查树——结论已体现在 diagnosis JSON 的 `hypotheses` 和 `ruled_out` 中。任务 Agent 再做一次排查树检索就是重复诊断，违反铁律。任务 Agent 新增的价值是**历史工单方案检索**——"之前有没有类似的工单？最后怎么解决的？"——这是提单 Agent 没做的。

| 函数 | 说明 | 触发条件 | 状态 |
|------|------|------|:---:|
| `retrieve_task_resolutions(problem_summary, hypotheses, fault_code, robot_type)` | Qdrant 语义检索相似已解决工单的最终方案 | diagnosis JSON 存在时自动调用 | ✅ 已有（待联调） |

---

### 4.4 能力三：讨论区理解

**用途**：@AI 讨论回复时需要读讨论历史来理解上下文；讨论摘要时需要读近期评论来生成摘要。诊断报告不需要。

**分类依据**：两个功能都用 `task_comments` 表的数据——只是用的方式不同（讨论回复基于历史推理、摘要是总结归纳）。都走同一个数据源。

| 函数 | 说明 | 触发条件 | 状态 |
|------|------|------|:---:|
| `load_recent_comments(task_id, limit=20)` | 读取最近 N 条评论 | @AI 讨论 / 摘要生成 | ✅ 已有（SQLAlchemy） |
| `load_new_comments_since(task_id, since_time)` | 读取某时间点后的新评论 | 判断是否有新讨论 | ✅ 新增 |

---

### 4.5 三功能能力对照

```
               附件分析    历史工单检索  讨论区理解
诊断报告         ✅          ✅          ✗
@AI 讨论         ✅          ✅          ✅
讨论摘要         ✗          ✗          ✅
```

各功能入口：

```python
# 诊断报告
async def diagnose(task_id):
    ctx = load_task_context(task_id)
    attachments_result = await analyze_attachments(ctx.attachments)  # 能力一
    history_result = await retrieve_task_resolutions(ctx.query_text) # 能力二
    return await llm.generate_report(ctx, attachments_result, history_result)

# @AI 讨论 — 按需调用全能力
async def discuss(task_id, query):
    ctx = load_task_context(task_id)
    history = await load_recent_comments(task_id)                # 能力三：必调
    # 能力一、二按需：工程师可能问附件或历史工单
    attachments_result = None
    history_result = None
    if _query_mentions_attachment(query, history):
        attachments_result = await analyze_attachments(ctx.attachments)
    if _query_mentions_history(query, history):
        history_result = await retrieve_task_resolutions(ctx.query_text)
    return await llm.respond_to_discussion(ctx, history, query, attachments_result, history_result)

# 讨论摘要
async def summarize(task_id):
    ctx = load_task_context(task_id)
    new_comments = await load_new_comments_since(task_id, last_summary) # 能力三
    return await llm.summarize_discussion(ctx, new_comments)
```

---

## 5. 诊断报告

诊断报告是 `[帮我分析]` 按钮的输出。不是聊天消息，是结构化文档。

### 报告格式

```markdown
## AI 诊断报告

**工单**：#44946 避让后车不动 | 潜伏车 | 高优先级
**分析时间**：2026-07-22 15:30

### 根因分析
MAPF v1.1.2 避让算法在特定场景下为被避让车生成起点=终点的占位路径，导致车辆无路径可执行。推理链：提单Agent诊断推测路径规划死锁 → 历史工单#44123确认同版本缺陷 → 日志14:40:02证实路径起点=终点 → 根因确认为MAPF版本缺陷。

### 建议步骤
1. 在 RCS 后台将 MAPF 版本回退至 v1.1.1
2. 设置 avoidance_distance_threshold >= 2.0m
3. 验证：手动触发一次避让场景，确认正常

### 证据
- 📋 提单Agent诊断：推测路径规划死锁，排除网络/硬件故障
- 📋 历史工单 #44123（相似度 0.89）：同一版本同一症状，回退版本后解决
- 📄 日志分析：robot.log 6行，提取4条异常，时间范围 14:40:02 ~ 14:40:05

### 使用的分析能力
- 附件分析（日志异常提取）
- 历史工单检索（相似已解决案例）

**置信度**：85%
```

### 存储

**不存库**。诊断报告即时生成，直接返回 JSON 给前端渲染为弹窗。工程师可以根据报告内容自行决定后续操作（在讨论区讨论、手动复制等）。讨论区的 @AI 回复和讨论摘要才会写入 `task_comments` 留存。

---

## 6. @AI 讨论回复

### 与诊断报告的区别

| | @AI 讨论 | [帮我分析] 诊断 |
|------|------|------|
| 触发 | 讨论区输入 | 按钮 |
| 上下文 | 讨论历史 + 工单 | 工单全量 + 附件 + KB |
| 使用能力 | 按需调用全部能力 | 自动调全部能力 |
| 输出 | 简短回复 → 写评论 | 完整报告 → 弹窗（不存库） |

### @AI 的 Prompt 设计

```
你正在参与工单 #44946「避让后车不动」的讨论。

## 工单背景
标题: 避让后车不动
描述: 44946避让生成的时候...
诊断: 推测路径规划死锁 / MAPF算法异常

## 近期讨论
[张工] 日志拿到了，帮我看看
[张工] 时间是14:40左右

## 用户消息
日志文件分析有什么需要注意的地方？

---
请用简洁的工程师口吻回复（≤300字），直接回答问题。
如果讨论中有明显的诊断线索，主动指出。
```

---

## 7. 讨论摘要

### 触发方式

1. **后台定时扫描**（diagnosis worker 内复用）：每 60 秒检查一次所有活跃工单的讨论更新
2. **手动触发**：前端按钮调用 `/api/ai/task/summarize`

### 判断"有新讨论"

```sql
-- 上次摘要之后的评论数
SELECT COUNT(*) FROM task_comments
WHERE task_id = X
  AND created_at > (上次摘要时间 OR 最近一条AI摘要的created_at)
  AND created_by != 'AI任务助手'  -- 不算AI自己的评论
```

有新评论（≥2条）→ 触发摘要生成。

### 摘要 Prompt

```
总结以下讨论的关键进展。只提取和工单解决相关的信息，忽略闲聊。

## 近期讨论
[张工 15:20] 日志拿到了，帮我看看
[李工 15:25] 应该是MAPF版本的问题
[张工 15:30] 确认了，v1.1.2有这个bug，回退到v1.1.1就好了

---
请用一句话总结（≤100字）：
```

---

## 8. 与前端的数据契约

### 前端需要的接口

| 前端动作 | API | 我们提供 |
|------|------|:---:|
| 点 [帮我分析] | `POST /api/ai/task/diagnose` | ✅ |
| 讨论区 @AI | `POST /api/ai/task/discuss` | ✅ |
| 获取讨论摘要 | `POST /api/ai/task/summarize` | ✅ |
| 提交解决方案 | `POST /api/ai/task/submit` | ✅ |
| 查看诊断报告 | `GET /api/tasks/{id}/comments`（后端，已有） | ✅ 不需要我们 |

### 工单详情页数据

前端从业务后端 `GET /api/tasks/{id}?load_comments=true` 获取：
- 工单基本信息（title/description/status/priority）
- 附件列表
- 所有评论（包括 "AI任务助手" 的诊断报告和讨论回复）
- 讨论摘要（前端自己在评论中筛选 `created_by="AI任务助手"` 且 `content` 以 "## 讨论摘要" 开头的）

**AI 模块不负责提供工单详情页数据**——全部由业务后端返回。

### 前端页面结构预期

```
工单详情页（前端独立页面，和「我要摇人」的工单详情分开）
├── 工单信息（从 GET /api/tasks/{id}）
├── [帮我分析] → POST /api/ai/task/diagnose
├── 讨论区
│   ├── 评论列表（从 GET /api/tasks/{id}?load_comments=true）
│   └── [输入框] [@AI 按钮] → POST /api/ai/task/discuss
└── 讨论摘要卡片（从评论中筛选AI生成的摘要）
```

---

## 9. v2.x → v3.0 变更清单

### 要砍掉的

| 组件 | 原因 |
|------|------|
| `pipeline.py:chat()` / `chat_stream()` | 大聊天功能取消 |
| `pipeline.py:_fetch_user_tasks_summary()` | 不需要感知用户全量工单 |
| `prompts.py:TASK_CHAT_SYSTEM_PROMPT` | Chat Prompt 不再需要 |
| `router.py:/api/ai/task/chat/stream` | 端点废弃 |
| `router.py:/api/ai/task/chat` | 端点废弃 |
| `router.py:/api/ai/task/analyze/stream` | 合并到 diagnose |
| `router.py:/api/ai/task/analyze` | 合并到 diagnose |
| `router.py:/api/ai/task/list` | 前端走业务后端 |
| `ChatPanel.tsx` taskId / username / token props | 前端不再需要 |
| `ChatPanel.tsx` 中有 taskId 路由的逻辑 | 前端不再需要 |

### 要新增的

| 组件 | 说明 |
|------|------|
| `pipeline.py:diagnose()` | 全能力诊断 → 返回报告 |
| `pipeline.py:discuss()` | @AI 讨论回复 |
| `pipeline.py:summarize()` | 讨论摘要 |
| `skills/` 目录 | LogParser / ZipExtractor / ImageAnalyzer / KnowledgeRetrieval / Summarizer |
| `router.py:/api/ai/task/diagnose` | 新端点 |
| `router.py:/api/ai/task/discuss` | 新端点 |
| `router.py:/api/ai/task/summarize` | 新端点 |
| `prompts.py:DIAGNOSE_PROMPT` | 诊断报告 Prompt |
| `prompts.py:DISCUSS_PROMPT` | @AI 讨论 Prompt |
| `prompts.py:SUMMARIZE_PROMPT` | 讨论摘要 Prompt |
| `schemas.py:DiagnosticReport` | 诊断报告模型 |

### 保留不变

| 组件 | 说明 |
|------|------|
| `diagnosis_service.py` | 后台自动诊断 worker（修改后适配新 diagnose 方法） |
| `_add_diagnosis_comment()` | 写 task_comments 的方法 |
| `_index_solution()` + `retrieval.py` 读/写 | 知识闭环 |
| `analyzer.py` / `attachment_parser.py` | 作为 Skill 复用 |
| `SolutionCard.tsx` | 保留给工单详情页展示诊断报告 |
| `submit()` | 方案提交 |

---

## 10. 实现计划

### Phase 1: 砍旧
- [ ] 移除 `chat()` / `chat_stream()` / `_fetch_user_tasks_summary()`
- [ ] 移除 Chat 相关 Prompt（`TASK_CHAT_SYSTEM_PROMPT`）
- [ ] 移除废弃端点（chat/stream, chat, analyze/stream, analyze, list）

### Phase 2: 三大新功能
- [ ] `diagnose()` — 全能力诊断，即时返回报告（不落库）
- [ ] `discuss()` — @AI 讨论回复（写 task_comments）
- [ ] `summarize()` — 讨论摘要（写 task_comments）
- [ ] 新增 3 个 Prompt + 3 个端点

### Phase 3: 前端对齐
- [ ] 前端 ChatPanel 移除 tasks 场景逻辑
- [ ] 工单详情页对接 3 个新端点
- [ ] 诊断报告弹窗渲染
