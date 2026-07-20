# 任务 Agent (AiTaskPlatform) 项目总览报告

> 日期：2026-07-20 | 分支：js_dev → develop | 负责人：AI 算法工程师

---

## 一、一句话概括

实现了面向接单工程师的 AI 助手（任务 Agent）——在「系统任务」页面通过 ChatPanel 自由技术问答，选中工单后自动加载诊断上下文并生成结构化方案草稿，工程师可编辑校准后提交。

---

## 二、代码变更清单

### 2.1 AI 服务（端口 8401）— 新建模块

```
ai/agents/AiTaskPlatform/                    ← 新建
├── TASK_AGENT_DESIGN.md      设计文档
├── PROJECT_OVERVIEW.md       本文件
├── __init__.py               公开导出
├── pipeline.py               核心类 AiTaskAgent (634 LOC)
│   ├── chat() / chat_stream()           自由问答
│   ├── analyze() / analyze_stream()     工单分析 + 方案生成
│   ├── submit()                         方案提交 + tickets 表更新
│   ├── _load_task_context()             从 tickets 表直读工单
│   ├── _run_analysis()                  三路并行分析编排
│   ├── _build_prompt() / _parse_solution()
│   └── _extract_log_errors()
├── schemas.py                9 个 Pydantic 模型 (123 LOC)
├── prompts.py                3 个 prompt 模板 (157 LOC)
├── analyzer.py               TaskAnalyzer 三路分析引擎 (198 LOC)
├── attachment_parser.py      日志 ERROR/WARN 提取 (171 LOC)
├── demo.py                   Mock 数据演示脚本
└── cli_chat.py               命令行交互工具（不提交）
```

### 2.2 AI 服务 — 修改的文件

| 文件 | 改动 | 行数 |
|------|------|:---:|
| `ai/api/router.py` | 新增 task_agent_router（7 个端点） | +233 |
| `ai/api/__init__.py` | 导出 task_agent_router | +4 |
| `ai/run.py` | 挂载 task_agent_router | +1 |
| `ai/core/embed.py` | 本地模型路径探测 `embed_models/` 目录 | +12 |
| `ai/AI_Service_Description.md` | 补入任务 Agent 章节 | +80 |

### 2.3 前端 — 新建文件

| 文件 | 说明 | 行数 |
|------|------|:---:|
| `frontend/src/shared/components/SolutionCard.tsx` | 可编辑方案卡片组件 | 127 |

### 2.4 前端 — 修改的文件

| 文件 | 改动 | 行数 |
|------|------|:---:|
| `frontend/src/shared/components/ChatPanel.tsx` | 场景切换 + Message 扩展 + 诊断注入 | +80 |
| `frontend/src/pages/tasks/TasksView.tsx` | 传 taskId/taskTitle/taskDescription | +6 |

### 2.5 已生成但不需要的改动

| 文件 | 说明 |
|------|------|
| `backend/app/modules/tasks/schemas/ticket.py` | 本计划加 diagnosis 字段，但后端同事确认工单数据在 AI 侧 tickets 表，不需要改 |

---

## 三、API 契约（AI 服务 8401）

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|:---:|
| POST | `/api/ai/task/chat/stream` | 自由问答 SSE | ✅ |
| POST | `/api/ai/task/chat` | 自由问答 非流式 | ✅ |
| POST | `/api/ai/task/analyze/stream` | 工单分析 SSE → SolutionDraft | ✅ |
| POST | `/api/ai/task/analyze` | 工单分析 非流式 | ✅ |
| POST | `/api/ai/task/submit` | 提交方案 → 更新 tickets 表 | ✅ |
| POST | `/api/ai/task/list` | 列出当前用户待处理工单 | ✅ |
| GET | `/api/ai/task/health` | 健康检查 | ✅ |

---

## 四、数据架构

### 4.1 数据流全貌

```
用户报障（Call 页面）
  │
  ▼
提单 Agent (AiDiagnosisPlatform)
  ├── 5 路 KB 检索 → 多轮诊断
  └── submit() → 写入 tickets 表
        ├── title, description, type, priority, status
        ├── diagnosis JSON: {problem_summary, hypotheses, ruled_out, collected_info}
        ├── robot_type, fault_code, location, attachments
        └── source = "ai_agent"

─────────────── tickets 表 ────────────────
  │
  ▼
任务 Agent (AiTaskPlatform)
  ├── ChatPanel 自由问答 (无 taskId → chat/stream)
  ├── ChatPanel 工单分析 (有 taskId → analyze/stream)
  │     ├── _load_task_context(): SQLAlchemy 读 tickets 表
  │     ├── _run_analysis(): 排查树结论 + 附件解析
  │     └── LLM → SolutionDraft → SSE 流式
  └── submit()
        ├── tickets.status = "resolved"
        └── tickets.diagnosis["solution"] = {...}

─────────────── tasks 表（业务后端 8400）───────────────
  │  TasksView 工单卡片列表：GET /api/tasks/ (后端查询 tickets 表)
  │  工单详情、催办、修改：PUT /api/tasks/{id} (后端)
  │  状态机校验、通知推送：后端负责
```

### 4.2 关键决策

| 决策 | 原因 |
|------|------|
| 任务 Agent 直读 tickets 表（不调后端 API） | 工单全量数据在 AI 模块的 tickets 表，无需跨服务调用 |
| 同一 session_id 贯穿 chat/analyze | Redis memory 共享对话上下文，无 taskId→有 taskId 切换不丢历史 |
| 前端 TasksView 工单列表走后端 API | 后端同事查询 tickets 表返回，AI 模块不负责前端列表渲染 |
| SolutionCard 在 ChatPanel 内渲染 | 不破坏现有气泡消息流，subtype 标识切换渲染分支 |

---

## 五、已验证的功能

| 测试项 | 方法 | 结果 |
|------|------|:---:|
| AI 服务启动 | `python ai/run.py` (8402) | ✅ |
| 健康检查 | `curl /api/ai/task/health` | ✅ 200 |
| 7 个路由注册 | TestClient | ✅ 全部注册 |
| schema 导入 | Python import | ✅ |
| _parse_solution() JSON | 正常/包裹/无效 JSON | ✅ |
| _extract_conclusion() | 排查树文本→【结论】提取 | ✅ |
| 完整 analyze() + LLM | Mock TaskContext | ✅ confidence=0.85 |
| SSE 事件格式 | TestClient stream | ✅ status×3→first_token→token→result→done |
| 附件解析: 日志 ERROR | 模拟 6 行日志含 4 异常 | ✅ |
| 附件解析: 截断 | 10000 行日志 | ✅ ≤2000 chars |
| chat() 自由问答 | "E1601 是什么意思" | ✅ 错误码解释 |
| chat() + 工单上下文 | "帮我总结工单" | ✅ 结合上下文回答 |
| 上下文连续性 | chat→analyze→chat 同一 session | ✅ 历史保留 |
| demo.py Mock 验证 | 你运行的 | ✅ confidence=0.92 |

---

## 六、未完成项（后续迭代）

| 优先 | 事项 | 阻塞 | 说明 |
|:---:|------|------|------|
| P0 | Qdrant task_resolutions collection | 无 | `retrieve_task_resolutions()` 新方法 + `_index_solution()` 实现 |
| P2 | 附件回放解析 | 无 | `attachment_parser.py` 骨架已有 |
| P2 | 前端联调验证 | MySQL 就绪 | TasksView + ChatPanel 完整流程 |

---

## 七、与团队协调状态

| 角色 | 事项 | 状态 |
|------|------|:---:|
| 前端 | ChatPanel 场景切换 + SolutionCard | ✅ 已完成（我们改的） |
| 前端 | TasksView 传 taskId | ✅ 已完成（我们改的） |
| 后端 | 工单列表 API (`GET /api/tasks/`) | ✅ 后端确认负责，查询 tickets 表 |
| 后端 | diagnosis JSON 读取 | ✅ 不需要后端——任务 Agent 直读 tickets 表 |
| 后端 | submit 状态更新 | ✅ 不需要后端——任务 Agent 直写 tickets 表 |
| 运维 | Nginx 配置 | ✅ 已有 `/api/ai/*` → 8401 转发 |

---

## 八、运行命令速查

```bash
# AI 服务启动
.venv\Scripts\python.exe ai\run.py

# 健康检查
curl http://localhost:8401/api/ai/task/health

# Mock 演示
.venv\Scripts\python.exe ai\agents\AiTaskPlatform\demo.py

# 命令行交互
.venv\Scripts\python.exe ai\agents\AiTaskPlatform\cli_chat.py
```
