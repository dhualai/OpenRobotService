# AiTaskPlatform — 任务 Agent 设计文档

> 版本：3.3 | 日期：2026-07-29
>
> **本文件是 AiTaskPlatform 的权威设计文档**，供开发时参考和每次新对话恢复上下文。
>
> **当前状态**：v3.0 架构重构——砍掉大聊天，任务 Agent 聚焦工单详情页。能力拆分为 Skill/Tool，@AI 讨论 + 诊断报告 + 讨论摘要。

---

## 更新日志

| 日期 | 版本 | 变更摘要 |
|------|:---:|------|
| 2026-07-29 | 3.3 | **附件解析器全类型扩展**：新增 tar/tgz/gz/docx/pdf/xlsx/md/json/xml/yaml/yml 支持；压缩包解压后送 LogSubAgent（非直接传二进制）；日志轮转后缀识别(.log.1/.log.16)；共享 `_extract_log_paths` 方法；新增 trace_attachments.py 全链路测试 |
| 2026-07-27 | 3.2 | **summarize 重构**：改为后端触发 + AI 自扫描模式（无参）；图片分析两阶段流水线（VLM+文本）；反幻觉约束；派单+日志子Agent 日志 |
| 2026-07-23 | 3.1 | **前端接入完成**：「帮我分析」→ 短链接 → Dialog；@AI 按钮自动填前缀；摘要纯展示；修复 task_id 422 + /diagnose router |
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

### 2.1 工程师进入工单详情页（v3.1 实际实现）

```
系统任务 → 点击工单卡片 → 进入独立工单详情页

页面布局（v3.1 实际）:
  ┌──────────────────────────────────────────┐
  │  工单 #42  系统故障排查                     │
  │  状态: 进行中  |  优先级: 高               │
  │  描述: ...                                 │
  │                                            │
  │  ── 🤖 AI 讨论摘要 ── [🤖 帮我分析] ──   │
  │  📝 摘要内容（后端定时写入，前端从评论提取） │
  │                                            │
  │  ── 讨论区 ────────────────────────────   │
  │  👤 张工: 日志拿到了，帮我看看             │
  │  🤖 AI任务助手: 📋 AI 诊断报告 — 点击查看… │
  │  👤 张工: @AI 找到问题了，MAPF版本太旧     │
  │                                            │
  │  [输入框] [@AI] [发送]                    │
  └──────────────────────────────────────────┘

关键交互：
  - [🤖 帮我分析] 在 AI 摘要卡片右上角 → 调 /diagnose → 讨论区插入短链接
  - [@AI] 按钮 → 自动在输入框填入 "@AI " 前缀 → 工程师补充问题 → [发送]
  - 讨论区发送时检测 @AI 前缀 → 调 /discuss；否则普通评论
  - 点击诊断短链接 → Dialog 弹窗展示完整报告
  - AI 摘要纯展示，后端定时触发 /summarize 写入 task_comments
```

### 2.2 工程师点击 [帮我分析]（v3.1 实际流程）

```
POST /api/ai/task/diagnose { task_id }
  │
  ├─ 1. 加载工单上下文（task_adapter.load_task_context_dict）
  │
  ├─ 2. 附件分析：日志 → LogSubAgent 多轮推理；非日志 → parse_attachments
  │
  ├─ 3. 历史工单检索（Qdrant task_resolutions 语义检索）
  │
  ├─ 4. LLM 综合分析 → 输出 { root_cause_analysis, suggested_actions, references, confidence }
  │
  └─ 5. 返回 JSON → 前端不写 task_comments
        └── 前端本地插入短链接到讨论区（不写后端）
             └── 点击短链接 → Dialog 弹窗展示完整报告
```

### 2.3 工程师 @AI 提问（v3.1 实际流程）

```
点击 [@AI] 按钮 → 输入框自动填入 "@AI " 前缀
  → 工程师补充问题 → 点击 [发送]
  → 前端检测到 @AI 前缀：
      ├─ 1. 先把用户消息写入 task_comments（普通评论）
      └─ 2. 调 POST /api/ai/task/discuss { task_id, query, context }
            │
            ├─ 加载讨论历史（最近 10 条 task_comments）
            ├─ 加载工单上下文
            ├─ 按需调 LogSubAgent / 附件分析 / 历史工单（关键词匹配）
            ├─ LLM 基于讨论+上下文回复 → 写 task_comments（AI任务助手）
            └─ 前端 loadDetail() 刷新评论列表
```

---

## 3. API 契约

### 端点一览（v3.1 实际状态）

| 方法 | 路径 | 说明 | 触发 | 落库 |
|------|------|------|------|:---:|
| POST | `/api/ai/task/diagnose` | 全能力诊断 → 即时返回报告 | [帮我分析] 按钮 | ❌ 不落库 |
| POST | `/api/ai/task/discuss` | @AI 讨论回复 → 写 task_comments | 讨论区 @AI | ✅ AI 回复写评论 |
| POST | `/api/ai/task/summarize` | 检测新讨论 → 生成摘要 → 写 task_comments | 后台定时 | ✅ 摘要写评论 |
| POST | `/api/ai/task/submit` | 提交方案 → 更新工单 + Qdrant 回写 | 工程师确认 | ✅ |
| GET | `/api/ai/task/health` | 健康检查 | 运维 | - |

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

### 4.2 能力一：附件分析 + 日志子Agent

**用途**：诊断报告生成时，分析工单附带的文件。@AI 讨论也按需调用。

**附件类型覆盖**（v3.3 全量）：

| 类别 | 扩展名 | 解析方式 |
|------|--------|---------|
| 压缩包 | `.zip` `.tar` `.tgz` `.gz` | 解压到临时目录 → 遍历内层文件 → 按类型提取 |
| 日志/文本 | `.txt` `.log` `.csv` `.log.1` `.log.16`… | ERROR/WARN 提取 + 时间戳范围（截断 100KB） |
| 文档 | `.docx` `.pdf` `.xlsx` `.md` | python-docx / pdfplumber→PyPDF2 / openpyxl→xml fallback / 纯文本 |
| 工程文件 | `.json` `.xml` `.yaml` `.yml` | 文本提取 + 结构摘要（顶层键/主要标签） |
| 图片 | `.jpg` `.jpeg` `.png` `.webp` `.bmp` `.gif` | 两阶段：VLM 看图描述 → 文本模型推理 |

**压缩包内文件保护**：非日志文件超过 100MB 自动跳过；日志文件不限大小（`_TEXT_CHUNK_LIMIT` 截断到 100KB）。

**管道拆分**（diagnose/discuss 共用）：

```
附件列表
  │
  ├── 日志组 (.log .txt .csv .log.1 / .zip .tar .tgz .gz)
  │     └── _extract_log_paths() → 解压到 tmpdir → 取内层日志路径
  │           └── LogSubAgent 多轮推理 → 结论注入 Prompt
  │           └── 完成后清理 tmpdir
  │
  ├── 非日志组 (docx/pdf/xlsx/md/json/xml/yaml/yml/图片等)
  │     └── parse_attachments() → AttachmentAnalysis
  │
  └── 图片 (.jpg/.png/.webp)
        └── analyze_images() → VLM 描述 + 文本推理
```

**关键函数**：

| 函数 | 说明 | 位置 |
|------|------|------|
| `_extract_log_paths(attachments)` | 共享方法：从附件列表提取日志路径（压缩包先解压） | `pipeline.py` |
| `parse_attachments(attachments)` | 入口：全类型附件 → `AttachmentAnalysis` | `attachments/parser.py` |
| `analyze_images(attachments, ctx)` | 图片两阶段分析（VLM+文本） | `attachments/parser.py` |
| `LogSubAgent.analyze(log_file, task_ctx)` | 日志多轮推理（知识库指引 + LogIndex 查询） | `log_analyzer/sub_agent.py` |

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

### 4.5 三功能能力对照（v3.3）

```
               附件分析    历史工单检索  讨论区理解  日志子Agent  代码检索
诊断报告         ✅          ✅          ✗          ✅          ✗
@AI 讨论         ✅          ✅          ✅          ✅          ✅
讨论摘要         ✗          ✗          ✅          ✗          ✗
```

**附件解析触发条件**（v3.3 实际实现）：
- diagnose：总是调用（全能力分析）
- discuss：仅当 query 含关键词时按需调用（日志/附件/图片/截图/屏幕等），且压缩包自动解压取内层日志路径
- summarize：不调用

各功能入口（v3.3 实际）：

```python
# 诊断报告
async def diagnose(task_id):
    ctx = load_task_context(task_id)
    log_paths, tmp_dirs = _extract_log_paths(ctx.attachments)  # 自动解压
    log_result = await LogSubAgent(log_paths[0]).analyze(...)   # 日志子Agent
    att_result = await parse_attachments(non_log_atts)          # 非日志附件
    img_result = await analyze_images(ctx.attachments)          # 图片VLM
    hist_result = await retrieve_task_resolutions(query)        # 历史工单
    shutil.rmtree(tmp_dirs)                                     # 清理临时目录
    return await llm.generate_report(ctx, log_result, att_result, img_result, hist_result)

# @AI 讨论 — 按需调用全能力
async def discuss(task_id, query, context):
    ctx = load_task_context(task_id)
    history = await load_recent_comments(task_id)               # 能力三：必调
    facultative = ""
    # 能力一、日志子Agent：按关键词匹配
    if any(kw in query for kw in log_keywords):
        log_paths, tmp_dirs = _extract_log_paths(ctx.attachments)
        log_result = await LogSubAgent(log_paths[0]).analyze(...)
        facultative += log_result.to_prompt_text()
        shutil.rmtree(tmp_dirs)
    if any(kw in query for kw in img_keywords):
        facultative += await analyze_images(ctx.attachments)
    if any(kw in query for kw in code_keywords):
        facultative += await code_skill.search(query)
    if any(kw in query for kw in hist_keywords):
        facultative += await retrieve_task_resolutions(query)
    return await llm.respond_to_discussion(ctx, history, query, facultative)

# 讨论摘要
async def summarize(task_id):
    ctx = load_task_context(task_id)
    new_comments = await load_new_comments_since(task_id, last_summary)  # 能力三
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

### 触发方式（v3.1）

后端定时（如每 3 分钟）调用 `POST /api/ai/task/summarize {}`，**无需传参数**。
AI 模块自行扫描所有 `status = in_progress` 的工单，逐条判断是否需要生成摘要。

### 判断"有新讨论"

AI 模块内部逻辑：
- 读 task_comments，找到最近一条 `📝 讨论摘要`（created_by = "AI任务助手"）
- 计算上次摘要后的新人类评论数
- **≥2 条** → 生成摘要 → 写 task_comments
- **<2 条** → 跳过

### 摘要 Prompt

```
总结以下讨论的关键进展。只提取和工单解决相关的信息，忽略闲聊。

## 近期讨论
[张工 15:20] 日志拿到了，帮我看看
[李工 15:25] 应该是MAPF版本的问题
[张工 15:30] 确认了，v1.1.2有这个bug，回退到v1.1.1就好了

---
请用一句话总结（≤150字）：
```

---

## 8. 与前端的数据契约

### 前端需要的接口（v3.1 实际状态）

| 前端动作 | API | 状态 |
|------|------|:---:|
| 点 [帮我分析] | `POST /api/ai/task/diagnose` | ✅ 已接入 |
| 讨论区 @AI | `POST /api/ai/task/discuss` | ✅ 已接入 |
| 获取讨论摘要 | 后端定时 `POST /api/ai/task/summarize` | ✅ 后端触发 |
| 提交解决方案 | `POST /api/ai/task/submit` | ✅ |
| 查看诊断报告 | 前端 Dialog 弹窗（本地 state，不写后端） | ✅ |

### 工单详情页数据（v3.1 实际）

前端从业务后端 `GET /api/tasks/{id}?load_comments=true` 获取：
- 工单基本信息（title/description/status/priority）
- 附件列表
- 所有评论（包括 "AI任务助手" 的 AI 回复和摘要）

**AI 摘要提取逻辑**（前端）：
- 筛选 `created_by === 'AI任务助手'` 的评论
- 取最新一条 `content` 以 `📝 讨论摘要` 开头的 → 展示在 AI 摘要卡片

**AI 模块不负责提供工单详情页数据**——全部由业务后端返回。

### 前端页面实际结构（v3.1）

```
工单详情页（TaskDetailPage.tsx）
├── 工单信息（从 GET /api/tasks/{id}）
├── AI 摘要卡片 → 右上角 [🤖 帮我分析] → POST /api/ai/task/diagnose
│   └── 摘要内容（从评论中提取 📝 讨论摘要）
├── 讨论区
│   ├── 评论列表（含 AI 诊断短链接）
│   │   └── 诊断短链接：📋 <a class="diagnosis-link"> → 点击 → Dialog 弹窗
│   └── [输入框] [@AI] [发送]
│       ├── [@AI] → 自动填入 "@AI " 前缀（不直接调API）
│       └── [发送] → 检测 @AI 前缀 → /discuss；否则普通评论
└── Dialog（AI 诊断报告：根因/建议/参考/置信度）
```

### 关键实现细节

1. **task_id 类型**：业务后端返回整数，前端 `String(detail.id)` 避免 Pydantic 422
2. **@AI 用户体验**：@AI 按钮只负责在输入框填前缀，工程师可继续打字，点发送才调 API
3. **@AI 消息双写**：用户 @AI 消息先写入 task_comments（普通评论），再调 /discuss，AI 回复也写入
4. **诊断短链接**：纯前端本地 state，不写 task_comments；只存活在当前会话
5. **摘要**：后端定时 summarize → 写入 comments → 前端 loadDetail 时自动提取

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

### Phase 1: 砍旧 ✅
- [x] 移除 `chat()` / `chat_stream()` / `_fetch_user_tasks_summary()`
- [x] 移除 Chat 相关 Prompt（`TASK_CHAT_SYSTEM_PROMPT`）
- [x] 移除废弃端点（chat/stream, chat, analyze/stream, analyze, list）

### Phase 2: 三大新功能 ✅
- [x] `diagnose()` — 全能力诊断，即时返回报告
- [x] `discuss()` — @AI 讨论回复（写 task_comments）
- [x] `summarize()` — 讨论摘要（写 task_comments）
- [x] 新增 3 个 Prompt + 3 个端点
- [x] router: 修复 `/diagnose`（原为错误的工单列表代码），新增 `/discuss`

### Phase 3: 前端对齐 ✅ (v3.1, 2026-07-23)
- [x] 工单详情页（TaskDetailPage.tsx）对接 /diagnose + /discuss
- [x] [🤖 帮我分析] 按钮放在 AI 摘要卡片右上角
- [x] [@AI] 按钮自动填入前缀，不直接调 API
- [x] 诊断报告以短链接形式插入讨论区 → 点击弹 Dialog
- [x] AI 摘要从评论中提取 `📝 讨论摘要` 展示
- [x] fix: task_id String() 转换避免 Pydantic 422

### 已知问题 / 注意事项
- `/diagnose` 使用的 router 代码在 merge 前是工单列表逻辑，已修复
- 前端 `detail.id` 是 number，发给 AI 服务必须 `String()` 否则 422
- 诊断短链接是前端本地 state，刷新页面后消失（预期行为）
- 压缩包附件先解压到 tmpdir 再送 LogSubAgent，不直接传二进制 zip
- 日志轮转文件 `.log.1` / `.log.16` 等后缀由 `_ext()` 统一识别为 `.log`
- 全链路测试脚本：`ai/tests/trace_attachments.py`
