# U老师 长期自记忆（Memory）设计方案

> 目标（2026-08-28）：让 **U老师**（AiTaskPlatform 任务 Agent）像 **OpenClaw（龙虾）** 一样拥有**属于自己的、能自己读写、跨工单/跨会话复用的长期记忆**——把"知识库检索不到、但值得沉淀的重要事项"记下来，在后续诊断/讨论/总结中用上。
>
> 本方案为**设计稿 + 落地指引**，供评审后实现（实现后更新状态）。

---

## ⚠️ 首版范围（已与业务确认，2026-08-28）

**首版只做一件事：用户 @U老师 明确要求要记住的内容（`directive` 主动记忆）。**

理由：**工单关闭/解决时本来就有自动知识沉淀**（`diagnosis_worker.py`：扫描已解决工单 → 提取根因+方案 → `index_task_resolution` 写入 Qdrant `project` 域历史沉淀）。若长期记忆再做"自动抽取/重复累计"，会与之**重复、做重**。因此首版**砍掉一切自动沉淀**，只保留：

- ✅ **用户 @U老师 明确说「记住 XX / 以后都用 XX / 记一下 XX」** → `memory_store(directive)`。
- ✅ **`memory_recall` + 自动注入**：让记下来的内容能在后续诊断/讨论中被用上（这是"用"，不是"自动沉淀"，不与工单关闭沉淀冲突）。

**首版明确不做（避免与知识沉淀重复）：**
- ❌ summarize 后 LLM 自动抽取判断/约定
- ❌ 讨论中实时触发词扫描（除"用户 @ 要记"本身）
- ❌ 跨工单重复问题自动累计（recurring）
- ❌ 自动"做梦/压缩"整理

**与工单关闭知识沉淀的分工：**
| 维度 | 工单关闭知识沉淀 | U老师 长期记忆（首版） |
|------|------------------|------------------------|
| 触发 | 工单 status→RESOLVED/CLOSED 自动 | 用户 @U老师 明确要记 |
| 内容 | 根因 + 解决步骤 + 错误码（结构化方案） | 用户指定的"记住 XX"（可能非结构化判断/约定） |
| 去向 | Qdrant `project` 域（task_resolutions） | 本地 Markdown + Qdrant `personal` 域 |
| 性质 | 该工单的可检索方案 | 跨工单/跨会话的通用记忆 |

---

## 0. 背景与差距

### 0.1 现状：`ai/core/memory.py`（MemoryManager）
当前 `MemoryManager` 是**会话级短期记忆**：
- 按 `session_id` 分桶，存 `{turns, metadata}` 到 Redis（内存兜底、MySQL 降级）。
- 记的是**单会话多轮对话上下文**，有 `max_turns` 截断。
- **不是**长期、跨工单的经验记忆；**无向量检索/召回**；**无 Markdown 持久文件**。
- AiTaskPlatform 目前只用到 `add_turn`/`_save_analysis_context`，本身是**每请求无状态**的。

### 0.2 OpenClaw（龙虾）的记忆模型（我们要对齐的能力）
| OpenClaw 概念 | 对我们的映射 |
|---------------|---------------|
| Agent 可调用的 `memory_store` 工具 | U老师 的新 `memory_store` 能力（自己记） |
| Agent 可调用的 `memory_recall` / `memory_search` | 新 `memory_recall` 能力（需要时取） |
| `MEMORY.md`（长期精华）+ `memory/YYYY-MM-DD.md`（流水） | **本地 Markdown** 存放记忆条目 |
| LanceDB/语义检索 + auto-recall | **Qdrant 向量索引** + 自动召回注入 |
| 记忆触发词（“记住/以后都用…”） | 中文触发正则 + LLM 抽取判定 |
| 重要性 / supersede / 去重 | `importance` + `status` 字段 |

---

## 1. 总体架构

```
                讨论/诊断/总结流程（U老师）
                        │
        ┌───────────────┼───────────────────┐
        │               │                   │
   memory_recall   memory_store       自动捕获器(extractor)
   (能力·需要时取)  (能力·自己记)      (确定性兜底:触发词/LLM抽取/重复累计)
        │               │                   │
        └───────────────┼───────────────────┘
                        ▼
              AgentMemoryService (agent_memory_service.py)
              ├── Markdown 文件层   ← 真源/可读 (ai/.../memory/)
              └── Qdrant 向量索引    ← 语义检索 (personal 域集合)
```

- **产品无关内核**：memory 能力不依赖 AiTaskAgent 实例，只依赖 `AgentMemoryService`。
- **服务端确定性兜底优先**：自动捕获不靠 Agent 自觉，先程序触发 + LLM 判定，失败不阻断主流程。

---

## 2. 存储层设计（本地 Markdown + Qdrant 双写）

### 2.1 Markdown 文件层（真源）
目录：`ai/agents/AiTaskPlatform/memory/`

```
memory/
├── MEMORY.md                     # 长期精华（active 条目汇总，供人工页/快速预览）
├── 2026-08-28.md                 # 每日流水（当天所有写入，含 superseded 历史）
└── entries/                      # 结构化单条目文件（可选，便于精确增删改）
    ├── mem_<id>.md
    └── ...
```

**单条目 Markdown 块约定**（与 `meta.json` 类似的 front-matter 风格）：
```markdown
<!-- id: <uuid>
     kind: fact | lesson | convention | recurring | directive
     importance: 0.8
     source: task|comment|session
     source_id: "322"
     created_at: 2026-08-28 14:30:00
     status: active | superseded | archived
-->
内容：<一句话/一段话的记忆内容>
出处：<从哪条讨论/工单/用户对话来的关键判断>
```

### 2.2 Qdrant 向量索引（可检索）
- 复用既有 `ai/core/retrieval.py` 的 `QdrantClientWrapper.upsert_to_collection` + `EmbedClient.embed`。
- **集合**：沿用五层 domain 中的 `personal` 域（`get_active_collection_for("personal")` / 自动创建），避免造新域。
- **point payload**：
```jsonc
{
  "mem_id": "<uuid>",
  "kind": "fact",
  "content": "<记忆内容>",
  "importance": 0.8,
  "source_task": "322",
  "created_at": "2026-08-28 14:30:00",
  "status": "active",
  "domain": "personal"
}
```
- **召回**：`search_dense(query, collection=personal)` → 过滤 `status=active` → 按 `importance` 加权的 top_k。

### 2.3 `AgentMemoryService` 接口
```python
class AgentMemoryService:
    async def ensure_collection() -> str            # 取/建 personal 集合
    async def store(content, kind, importance=0.7, source="", source_id="") -> str   # 双写，返回 mem_id
    async def recall(query, top_k=3, kinds=None) -> list[dict]   # 语义检索，过滤 active
    async def supersede(mem_id) -> None              # 标记 superseded（不追加矛盾条目）
    async def delete(mem_id) -> None
    async def list_entries(kind=None, status="active") -> list[dict]
```
> 单例获取：`get_agent_memory_service()`（懒加载，类似 `get_retrieval_service`）。

---

## 3. 记忆条目种类（schema）

| kind | 含义 | 何时写入 | 例子 |
|------|------|----------|------|
| `fact` | 可查但知识库没有的事实/特征 | 诊断/讨论中发现特定车型、产品、错误码的稳定特征 | “USP 2.5 某错误码在新固件下是探空失败” |
| `lesson` | 经验/教训（可复用根因、踩坑） | 诊断出根因、复盘结论时 | “这类等待超时要先查 X 缓存，别先改超时” |
| `convention` | 口口相传的判断/约定/决策 | **工程师在讨论/总结中给的关键判断**（用户强调点） | “接单人说这类现场统一按 Y 处理” |
**首版只实际使用 `directive`**（用户 @U老师 明确要记），其余种类保留在 schema 中作为扩展位（后续如需再开放自动捕获）。

| kind | 含义 | 首版是否启用 | 例子 |
|------|------|--------------|------|
| `directive` | **用户明确要记的指令** | ✅（唯一启用） | “记住：XX客户统一按 Y 处理”；“以后派单优先给该接单人” |
| `fact` | 知识库查不到的事实/特征 | 🔒 预留 | “USP 某错误码在新固件下是探空失败” |
| `lesson` | 经验/教训 | 🔒 预留 | “这类等待超时要先查 X 缓存” |
| `convention` | 人为判断/约定 | 🔒 预留 | “接单人说现场统一按 Y 处理” |
| `recurring` | 跨工单重复问题 | 🔒 预留 | “A 型号报 B 问题频次变高” |

字段清单：`id / kind / content / importance / source / source_id / created_at / status`。

---

## 4. 读写能力（Capability 化）

新增两个 `BaseCapability` 子类（继承即自动注册进 `CapabilityRegistry`）：

### 4.1 `memory_store`（MemoryStoreCapability）
```python
name = "memory_store"
description = (
    "把一条重要信息存入 U老师 的长期记忆。适用：诊断/讨论中得出可复用的"
    "经验、教训、车型/错误码特征、工程师给的关键约定或判断，且该内容知识库"
    "检索不到、值得在后续跨工单复用时应存入。输入: content 记忆内容, kind 种类"
    "(fact/lesson/convention/recurring/directive), importance 重要度0-1。"
)
tags = ["memory", "记忆", "记住", "经验", "约定"]
# run(**kwargs): content/kind/importance → service.store(...) → CapabilityResult(text="已记住：...")
```
### 4.2 `memory_recall`（MemoryRecallCapability）
```python
name = "memory_recall"
description = (
    "检索 U老师 此前自己记下的长期记忆。适用：当当前问题可能与之前处理过的"
    "经验/教训/约定相关、而知识库或历史工单检索不到时，主动查询记忆。"
    "输入: query 检索内容, top_k(默认3)。输出: 相关记忆条目（含种类/重要度）。"
)
tags = ["memory", "记忆", "经验", "记住", "惯例"]
# run(**kwargs): query → service.recall(...) → 格式化文本
```

### 4.3 如何被调度（首版）
- **`memory_store`**：**唯一触发点 = 用户 @U老师 明确要记**。在 `discuss` 回复流程里，先做**触发词判定**：若用户话里带「记住/记一下/以后都用/以后就用/记住这条/统一…」等 → 提取要记的内容 → 调用 `memory_store(kind=directive, content=…)`，并在回复中确认“已记住”。
- **`memory_recall`**：加入 `CapabilityRegistry`，在 `discuss`/`diagnose` 的 `available_caps` 里**默认可用**；同时在上下文构建处**确定性自动召回**一次（见 §6），让记下来的内容跨工单/跨会话被用上。
- 两个能力都**无外部资源依赖**，在 `_RESOURCE_GUARD` 外视为始终可用。

---

## 5. 记忆写入触发（首版：仅用户主动 @ 要记）

**不做自动抽取器。** 首版唯一的记忆入口是用户明确表达“要记住”。

### 5.1 触发判定（discuss_flow 内嵌）
在 `discuss_flow` 收到用户 query 时，先用触发正则判定是否需要记忆：
```python
_MEMORY_SAVE_RE = re.compile(
    r"(?:记住|记一下|帮我记|以后都用|以后就用|以后都按|记住这条|记下|别忘|统一(?:按|用|走)|约定(?:是|为)?)",
    re.IGNORECASE,
)
```
- 命中 → 从 query 里**剔除触发词后剩下的内容**作为要记的 `content`（保留原句更稳，去掉“记住/帮我记一下”等指令前缀）。
- 因为语义可能有模糊（如“记住，不是这个”），**给 LLM 一次确认/提炼**：`memory_extract` 用一次轻量 LLM 调用，抽取“要记的对象 + 内容 + 校验是否真是要记”，输出 `{should_save, content, kind=directive}`；`should_save=False` 则不记（防误记）。
- 写入 `memory_store`（异步 best-effort，失败不阻断回复），并在回复里带一句“✅ 已记住：…”。

### 5.2 去重 / supersede
- 写入前用 `recall(content)` 近似查重：若已有高度相似的 active `directive` → **原地 `supersede` 旧的、写入最新**（对齐龙虾 “supersede in place”，不产生矛盾双 active）。

---

## 6. 召回注入（U老师 真正用起来）

在 `discuss_flow` / `diagnose_flow` 的上下文/`task_ctx_for_plan` 构建处，增加：
```python
mem_block = await self._memory_service.recall(query=用户问题/工单描述, top_k=3)
```
把结果作为「**U老师 此前记忆】（仅供参考，来源: comment/task）」区块注入 prompt，与既有「历史工单方案 / 排查树 / 知识库参考」并列。这样**即使能力没被派发，记忆也能被看到**（确定性兜底）。

---

## 7. 涉及文件

**新增**
- `ai/agents/AiTaskPlatform/memory/__init__.py`
- `ai/agents/AiTaskPlatform/memory/agent_memory_service.py`（Markdown 双写 + Qdrant 检索 + 单例）
- `ai/agents/AiTaskPlatform/capabilities/tools/memory_store.py`
- `ai/agents/AiTaskPlatform/capabilities/tools/memory_recall.py`

**修改**
- `ai/agents/AiTaskPlatform/capabilities/__init__.py`（导出 + `__all__`）
- `ai/agents/AiTaskPlatform/handlers/discuss_flow.py`（触发词判定 + 记忆写入 + 召回注入）
- `ai/agents/AiTaskPlatform/handlers/diagnose_flow.py`（召回注入）
- `ai/agents/AiTaskPlatform/pipeline.py`（持有 `_memory_service` 单例）
- `ai/config.py`（可选：`memory_dir`、personal 域指针已可复用）

> 注：`extractor.py` 自动抽取器、`summarize_flow` 记忆抽取**首版不做**（避免与工单关闭知识沉淀重复）。

---

## 8. 明确不做（Scope）
- **不做任何自动沉淀/抽取**：用户 @ 要记以外的自动记忆不实现（与工单关闭知识沉淀 `diagnosis_worker.py` 分工）。
- **不做 `recurring`/自动重复累计**、不做 `convention`/`fact` 自动识别（schema 预留，后续如需再开）。
- **不跨 Agent 共享**：沿用既有 D13，只服务 AiTaskPlatform。
- **不上 MCP**：本版纯自研，能力对齐 Claw 的工具形态。
- **不做 UI 管理界面**：先保证读写链路；查看/编辑/删除后续加轻量 CLI/API。
- **不做自动“做梦/压缩”**（龙虾 dreaming）：后续再沉淀整理到 `MEMORY.md`。

---

## 9. 验收标准（Done）
1. `AgentMemoryService.store/recall` 单测通过（写入 Markdown + Qdrant，召回命中正确、`status=active` 过滤生效）。
2. `memory_store` / `memory_recall` 能力注册进 `CapabilityRegistry.list_available()`。
3. 用户 @U老师 说「记住 XXX」→ 触发 `directive` 记忆写入 + 回复确认“已记住”。
4. 后续 `discuss`/`diagnose` 中，涉及该记忆时能把相关记忆带进 prompt（自动注入或 `memory_recall`）并引用到。
5. 全部写入异步兜底：Qdrant/Markdown 失败不阻塞诊断/讨论主请求。
6. 与工单关闭知识沉淀 `diagnosis_worker.py` 无重叠（本次未改动其逻辑）。
