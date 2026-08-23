# ORS 算法模块分布图

> 开放机器人服务平台（OpenRobotService · 摇人吧服务号）—— 算法相关模块
> 覆盖 **AI 诊断平台** 与 **系统任务平台** 两个 Agent 平台。
> 依据 `ai/agents/AiDiagnosisPlatform/`、`ai/agents/AiTaskPlatform/`、`ai/core/` 代码梳理。

---

## 总览

```
┌────────────────────────────────────────────────────────────────────┐
│                    ORS 服务号 · 算法相关模块                          │
├───────────────────────────────┬────────────────────────────────────┤
│         AI 诊断平台             │          系统任务平台                 │
│   ai/agents/AiDiagnosisPlatform│  ai/agents/AiTaskPlatform           │
│   （我要摇人入口：诊断→转工单→派单）│  （系统任务入口：接单→分析→方案→沉淀） │
│                               │                                    │
│   ├─ 诊断 Agent 流水线         │  ├─ 任务 Agent 核心流水线            │
│   ├─ 智能派单 assigner         │  ├─ 能力单元（附件/日志/代码/检索）    │
│   └─ 派单后台 Worker           │  └─ 后台 Worker（诊断/知识沉淀）      │
├───────────────────────────────┴────────────────────────────────────┤
│                          AI 共享核心层                               │
│   ai/core/（LLM · Embedding · 检索 · 记忆 · 项目匹配 · 任务适配）      │
│   ai/api/（/api/ai/* 统一路由）· ai/run.py（服务启动 + Worker 装配）    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 一、AI 诊断平台

> 代码：`ai/agents/AiDiagnosisPlatform/`
> 定位：服务号「我要摇人」入口的 AI 在线诊断——知识库排查 → 追问 → 生成工单 → 自动派单。

### 1.1 诊断 Agent（纯 Agent 流水线）— `pipeline.py`

| 子模块 | 职责 |
|------|------|
| **状态管理 AgentState** | 持续理解问题：问题摘要 / 已排除原因 / 当前推测 / 已收集信息 / 工单就绪判定 |
| **意图识别** | LLM 决策 howto（操作咨询）/ troubleshoot（故障排查）/ chat（闲聊） |
| **知识检索** | 三路域检索（team/company/industry）+ 车端错误码精确检索 + 本地图片路径重写为 CDN URL |
| **工单类型跟踪** | LLM 逐轮维护 problem/bug/feature/support/other，锁定后不漂移 |
| **动态必填字段** | `_decide_ticket_fields` 转单时 LLM 按问题类型自选 2-3 个必补字段 |
| **提单前信息回填** | `_backfill_collected_info` 从对话提取用户已给出字段补入 collected_info |
| **工单填写模式** | ticket_collecting：聚焦补齐缺失字段，跳过检索 + 精简 Prompt 提速 |
| **项目匹配** | `_resolve_project` 用户简称 → 项目库匹配真实全名（单候选直配 / 多候选 LLM 裁决） |
| **闭环保护** | `_can_submit` 基于 last_submitted_ticket + 新问题，防重复提单 |
| **防鬼打墙** | 诊断轮次上限 6、收集轮次上限 4 |
| **双路径提单** | 对话路径（LLM action=submit）/ 按钮路径（prepare→confirm 弹窗）共用状态收尾 |
| **工单落库** | `task_adapter.upsert_task` → tasks 表（source=ai，按 external_id 幂等） |
| **派单触发** | 提单后加入待派单池 + Redis 发布 new_ticket 事件 |
| **标题生成** | 第 2 轮后 LLM 生成会话标题，同步 DB |

### 1.2 智能派单（assigner）— `assigner/`

**配置中心** `config/config.yaml → settings.py`：
模块关键词 / 模块锚文本 / 三路召回权重（0.70/0.20/0.10）/ 职级折扣 / 部门关键词三级 / 部门故障场景库 / 置信度阈值（auto≥0.8 / recommend≥0.5）/ 负载均衡参数

**DispatchFlow 主流程** `pipeline/dispatch_flow.py`：

```
Step 0  提单人指定（强信号正则"指定处理人：X" + LLM 弱信号兜底）
Step 1  部门过滤（关键词 strong/medium/weak 三级 + embedding 场景补漏，阈值 0.65）
Step 2  排除提单人（常规派单不派给自己）
Step 3  三路召回（并行）
          ├─ L1 纯 LLM 召回（权重 0.70）
          ├─ L2 语义召回   （权重 0.20）Embedding 工单→模块锚文本→反查工程师
          └─ L3 历史召回   （权重 0.10）A路相似工单聚人 + B路问题域聚人（归一化融合）
Step 4  精排 + 职级折扣（L2 打 6 折、L3 打 4 折）
Step 5  负载均衡（按在途工单数打折，系数=1/(1+在途×0.15)，30s 缓存）
Step 6  LLM 综合决策（成功返回 / 失败走 Step 7）
Step 7  规则兜底决策（auto / recommend / fallback 阈值判定）
```

| 组件目录 | 职责 |
|------|------|
| `recall/` | LlmRecall(L1) · SemanticRecall(L2) · HistoryRecall(L3-A 相似工单) + ExpertiseRecall(L3-B 问题域) |
| `ranking/` | Ranker 三路加权精排 · LlmDecision 综合决策 · FallbackDecision 规则兜底 |
| `filtering/` | department_filter 部门三大类硬过滤（硬件/车端软件/调度） |
| `sync/` | engineers_sync 人员画像（users 表+4字段）· history_sync 历史工单同步 · history_indexer 历史向量索引 |
| `eval/` | build_dataset 构建评估集 · run_eval 派单效果评估 |

### 1.3 派单后台 Worker — `assigner/pipeline/worker.py`

- **Redis Pub/Sub 事件驱动**：订阅 `usp:new_ticket`，新工单立即派单
- **定时扫描兜底**：每 N 秒扫 MySQL 待派单工单（防丢消息/重启遗漏）
- **写回 DB**：assigned_to + metadata_info（指派理由/置信度/决策类型）+ 状态流转
- **回调通知**：派单写回后回调后端内部接口发新建工单通知

---

## 二、系统任务平台

> 代码：`ai/agents/AiTaskPlatform/`
> 定位：服务号「系统任务」入口的接单工程师 AI 助手——基于工单诊断信息生成解决方案草稿，人工校准后提交，形成知识闭环。

### 2.1 任务 Agent 核心流水线 — `pipeline.py`

| 能力 | 触发方式 | 说明 |
|------|------|------|
| **方案草稿** analyze / analyze_stream | 接口 | 上下文加载 → 三路分析 → LLM 生成结构化 SolutionDraft |
| **诊断报告** diagnose | [帮我分析] 按钮 | 全能力分析（附件/日志/图片/历史工单）→ 即时返回报告（不落库） |
| **@AI 讨论** discuss | 讨论区 @AI | 基于讨论历史 + 工单上下文，按需调日志/图片/代码/历史检索，写 task_comments |
| **讨论摘要** summarize | 后台定时 | 扫描活跃工单 → 新评论 ≥2 条生成摘要 → 写 metadata_info.ai_summary |
| **方案提交** submit | 工程师确认 | 方案 → Qdrant task_resolutions 回写 + tasks 表状态更新 |
| **上下文加载** | 公共 | task_adapter.load_task_context_dict 读取工单全量（诊断结果/附件/车型/故障码） |

**三路并行分析** `_run_analysis`：
1. 排查树结论检索（仅取结论节点根因+方案，AGV/USP 问题）
2. 历史工单方案检索（Qdrant task_resolutions，相似已解决案例）
3. 附件解析（日志/回放关键信息）
> 平台自身问题（命中服务号关键词）→ 改查平台参考文档，跳过排查树。

### 2.2 能力单元

| 能力 | 代码 | 说明 |
|------|------|------|
| **附件分析** | `attachments/parser.py` | 全类型：压缩包(解压) / 日志文本 / 文档(docx/pdf/xlsx/md) / 工程文件(json/xml/yaml) / 图片(两阶段 VLM+文本) |
| **日志子 Agent** | `log_analyzer/` | LogSubAgent 独立多轮推理（≤8 轮）：知识库指引 + LogIndex 毫秒级查询 451MB 算法日志 |
| **代码检索** | `code_skill/` | CodeIndexer 建索引 + CodeRetriever 源码语义检索（@AI 讨论关键词触发） |
| **分析引擎** | `analysis/engine.py` | 三路并行分析编排容器 |
| **平台参考检索** | `retrieval.retrieve_platform_reference` | 服务号自身问题查 platform_manual / engineer_guide |

### 2.3 后台服务 — `services/diagnosis_worker.py`

- **诊断扫描 Worker**：定时扫描 tasks 表新工单（source=ai、未诊断）→ 触发三路分析 → 写 U老师评论（逐单串行防 LLM 并发过载）
- **知识沉淀 Worker**：扫描已解决工单 → 方案回写 Qdrant（知识闭环）

---

## 三、AI 共享核心层 — `ai/core/`

| 组件 | 代码 | 职责 |
|------|------|------|
| **LLM 客户端** | `core/llm.py` | LLMClient 统一封装 · DeepSeek/OpenAI Provider · complete / stream / stream_vision |
| **Embedding** | `core/embed.py` | EmbedClient 向量化（bge-small-zh-v1.5） |
| **检索服务** | `core/retrieval.py` | Qdrant 混合检索（稠密向量 + BM25 稀疏 + RRF 融合）· 多 collection：team 各域 / FAQ / 车端错误码 / 翻译表 / 排查树 / 任务方案 / 派单历史 |
| **会话记忆** | `core/memory.py` | MemoryManager Redis 记忆（turns + agent_state + 待派单池）· 指代消解 |
| **项目匹配** | `core/project_matcher.py` | 项目简称 → 标准名匹配 |
| **任务适配** | `core/task_adapter.py` | tasks 表读写：工单快照 / 状态更新 / 上下文加载 / 方案回写 |
| **对话存储** | `core/conversation_store.py` | 会话标题同步 DB |
| **MinIO 客户端** | `core/minio_client.py` | 附件上传/预签名 URL/下载 |

---

## 四、API 层与服务装配

### API 路由 `ai/api/router.py`

| 前缀 | 归属平台 | 能力 |
|------|---------|------|
| `/api/ai/qa/*` | AI 诊断平台 | 问答（含诊断追问与提单）/ 流式 SSE / 上传 / 工单（提交/草稿/确认/回执） |
| `/api/ai/task/*` | 系统任务平台 | 诊断报告 / @AI 讨论 / 讨论摘要 / 方案提交 / 健康检查 |
| `/api/ai/chat/*` | 共享 | 纯 LLM 对话 |
| `/api/ai/memory/*` | 共享 | 会话历史 / 待派单列表 / 历史工单查询 |
| `/api/ai/wecom/*` | 共享 | 企业微信 Smartsheet 项目同步 |

### 服务启动 `ai/run.py`

FastAPI lifespan 装配三个后台 Worker：
1. **派单 Worker**（AI 诊断平台）—— Redis 事件驱动 + 定时扫描
2. **诊断 Worker**（系统任务平台）—— 自动扫描新工单生成诊断评论
3. **知识沉淀 Worker**（系统任务平台）—— 已解决工单回写 Qdrant

---

## 五、两平台分工边界

| 维度 | AI 诊断平台 | 系统任务平台 |
|------|------------|-------------|
| 服务入口 | 「我要摇人」 | 「系统任务」 |
| 用户角色 | 现场/客户/管理 | 接单工程师 |
| 核心动作 | 诊断 → 转工单 → 派单 | 接单 → 分析 → 方案 → 提交 |
| 检索目标 | 知识库（FAQ/手册/错误码/翻译表） | 排查树结论 + 历史工单方案 + 附件 |
| 关键输出 | 工单（tasks 表）+ 派单结果 | 方案草稿（SolutionDraft）+ 讨论/摘要 |
| 知识回写 | 工单落库触发派单 | 已解决方案回写 Qdrant（task_resolutions） |
