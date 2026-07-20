"""Update AI_Service_Description.md to include AiTaskPlatform agent."""
import re

with open('ai/AI_Service_Description.md', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Bump version
content = content.replace(
    '> 版本：1.1 | 更新日期：2026-07-20',
    '> 版本：1.2 | 更新日期：2026-07-20'
)

# 2. Insert task agent in TOC
content = content.replace(
    '5. [数据分析平台 (`agents/AiDataAnalysisPlatform/`)](#5-数据分析平台-agentsaidataanalysisplatform)',
    '5. [任务 Agent (`agents/AiTaskPlatform/`)](#5-任务-agent-agentsaitaskplatform)\n'
    '6. [数据分析平台 (`agents/AiDataAnalysisPlatform/`)](#6-数据分析平台-agentsaidataanalysisplatform)'
)

# 3. Update API section number 6→7
content = content.replace(
    '6. [API 路由 (`api/`)](#6-api-路由-api)',
    '7. [API 路由 (`api/`)](#7-api-路由-api)'
)

# 4. Update remaining section numbers
content = content.replace(
    '7. [知识库入库 (`ingestion/`)](#7-知识库入库-ingestion)',
    '8. [知识库入库 (`ingestion/`)](#8-知识库入库-ingestion)'
)
content = content.replace(
    '8. [配置系统 (`config.py`)](#8-配置系统-configpy)',
    '9. [配置系统 (`config.py`)](#9-配置系统-configpy)'
)
content = content.replace(
    '9. [启动流程 (`run.py`)](#9-启动流程-runpy)',
    '10. [启动流程 (`run.py`)](#10-启动流程-runpy)'
)
content = content.replace(
    '10. [数据流全景](#10-数据流全景)',
    '11. [数据流全景](#11-数据流全景)'
)

# 5. Add task agent to architecture overview
content = content.replace(
    '│   ├── assigner/ 智能派单子模块（自动推荐负责人）            │\n│   └── 工単生成 + MySQL 入库 + 自动派单                       │',
    '│   ├── assigner/ 智能派单子模块（自动推荐负责人）            │\n│   └── 工単生成 + MySQL 入库 + 自动派单                       │\n└──────────────────────┬───────────────────────────────────┘\n                       │\n┌──────────────────────▼───────────────────────────────────┐\n│                   任务 Agent 层                            │\n│   AiTaskPlatform (pipeline.py)                            │\n│   ├── 工単上下文加载（diagnosis + tasks 表）              │\n│   ├── 多路分析（KB 检索 + 附件解析 + 历史方案检索）        │\n│   ├── LLM 生成结构化解决方案草稿                           │\n│   └── 人工校准 → 提交 → 知识库闭环                         │'
)

# 6. Add task agent to directory tree
content = content.replace(
    '│   └── AiDataAnalysisPlatform/',
    '│   ├── AiTaskPlatform/\n│   │   ├── TASK_AGENT_DESIGN.md # 设计文档\n│   │   ├── pipeline.py      # 任务分析流水线\n│   │   ├── schemas.py       # TaskContext / SolutionDraft\n│   │   ├── prompts.py       # 方案生成 prompt 模板\n│   │   ├── analyzer.py      # 多路分析编排\n│   │   └── attachment_parser.py # 附件解析（日志/回放）\n│   └── AiDataAnalysisPlatform/'
)

# 7. Add task agent section before data analysis platform
old_s5 = '\n## 5. 数据分析平台'
new_s5 = '''
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

> 完整设计见 `ai/agents/AiTaskPlatform/TASK_AGENT_DESIGN.md`

## 6. 数据分析平台'''
content = content.replace(old_s5, new_s5)

# 8. Fix section headings after the insertion
replacements = [
    ('## 6. API 路由 (`api/`)', '## 8. API 路由 (`api/`)'),
    ('### 6.1 诊断 Agent', '### 8.1 诊断 Agent'),
    ('### 6.2 LLM 对话', '### 8.2 LLM 对话'),
    ('### 6.3 会话记忆', '### 8.3 会话记忆'),
    ('### 6.4 智能派单', '### 8.4 智能派单'),
    ('### 7.1 统一入口', '### 9.1 统一入口'),
    ('### 7.2 各知识库详情', '### 9.2 各知识库详情'),
    ('### 7.3 Collection 热更新机制', '### 9.3 Collection 热更新机制'),
    ('### 7.4 排查树线性化格式', '### 9.4 排查树线性化格式'),
    ('## 7. 知识库入库', '## 9. 知识库入库'),
    ('## 8. 配置系统 (`config.py`)', '## 10. 配置系统 (`config.py`)'),
    ('## 9. 启动流程 (`run.py`)', '## 11. 启动流程 (`run.py`)'),
    ('## 10. 数据流全景', '## 12. 数据流全景'),
    ('### 10.1 用户提问', '### 12.1 用户提问'),
    ('### 10.2 生成工单', '### 12.2 生成工单'),
    ('### 10.3 流式 SSE', '### 12.3 流式 SSE'),
]
for old, new in replacements:
    content = content.replace(old, new)

# 9. Add task agent API section before knowledge base ingestion
content = content.replace(
    '### 9.1 统一入口',
    '''### 8.5 任务 Agent (`/api/ai/task`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/task/list` | 列出当前用户待处理工单 (Agent 视角) |
| POST | `/api/ai/task/analyze` | 分析工单 → 生成方案草稿 |
| POST | `/api/ai/task/analyze/stream` | SSE 流式分析 |
| POST | `/api/ai/task/submit` | 确认方案 → 保存 + 回写知识库 |
| GET | `/api/ai/task/health` | 健康检查 |

### 9.1 统一入口'''
)

# 10. Add task agent data flow after SSE section
content = content.replace(
    '## 附录 A',
    '''### 12.4 任务 Agent — 工单分析

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

## 附录 A'''
)

# 11. Add AiTaskAgent to appendix B
content = content.replace(
    '| `AiDiagnosisPlatform` | `get_diagnosis_platform()` | 诊断 Agent（提单） |',
    '| `AiDiagnosisPlatform` | `get_diagnosis_platform()` | 诊断 Agent（提单） |\n| `AiTaskAgent` | (待实现) | 任务 Agent（方案生成） |'
)

with open('ai/AI_Service_Description.md', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done updating AI_Service_Description.md')
