# 提单/知识问答 Agent 版本迭代记录

> 对应 Agent：AiDiagnosisPlatform（提单 Agent — 我要摇人 / 需求视角）
> Owner：知识库/问答 Agent 工程师
> 
> **本文档中「Assigner 子模块」章节由任务 Agent 工程师维护**（Assigner 于 2026-07-20 从旧 `basic_function/` 迁移至 `ai/agents/AiDiagnosisPlatform/assigner/`，归属提单 Agent，实际维护由任务 Agent 工程师负责）。

---

## 提单 Agent 主线

### v1.4 · 2026-07-22 ~ 2026-07-23

- **变更类型**：流程逻辑变更 + Prompt 调整 + 新增知识源
- **变更内容**：
  - **自动提单 + 服务端兜底**：LLM 输出 `action: "submit"` 时自动调用 `self.submit()` 生成工单；另加服务端关键词检测（"转工单""转单""提单"等），无论 LLM 是否输出 submit 都强制提单
  - **多工单支持**：新增 `ticket_seq` 计数器，`external_id` 格式由 `session_id` 变为 `session_id#seq`，同一会话可独立创建多个工单不再互相覆盖
  - **补充信息追加**：提单后用户发送的补充信息（文字/附件）自动追加到最新工单的 `description` 字段。双层检测：① LLM 输出 `intent="follow_up"` ② 兜底：`action="answer"` 且 intent 非 troubleshoot/howto/chat
  - **闲聊收尾短接**：纯问候/致谢/结束语（"ok""感谢""好的"等）正则匹配后直接返回短回复，跳过检索和 LLM 调用
  - **Platform FAQ 知识库**：新增第 6 路检索（`retrieve_platform_faq`），覆盖摇人吧平台自身 FAQ（工单类型/流转/角色/功能入口/转工单方式等 8 条），6 路并行检索
  - **DIAGNOSIS_PROMPT 分层**：身份+核心规则保留在 system prompt，平台 FAQ 细节下沉到知识库按需检索
  - **转工单 Prompt 强化**：转工单规则提升至最高优先级（⛔ 标记），独立于 howto/troubleshoot/chat 意图判断；follow_up 场景禁止追问/排查/给建议
  - **DB 导入解耦**：`ai/` 模块中 `from app.core.database import SessionLocal` 全部改为 `from app.core.db import SessionLocal`，不再间接依赖 identity_service → security → jose 认证链
- **变更原因**：
  - LLM 频繁 role-play 提单（回复"已生成工单"但 JSON 里 `action="answer"`），需服务端强制兜底
  - 原 `upsert_task` 按 `session_id` 唯一键 upsert，同一会话第 2 次转单会覆盖第 1 个工单
  - 用户提单后发送补充信息，agent 仍在继续排查引导而非直接记录
- **效果对比**：自动提单成功率 100%（关键词兜底）；同一会话可生成多个独立工单；补充信息正确追加到对应工单；闲聊收尾不再触发排查
- **回滚方式**：移除 `pipeline.py` 中 `_force_submit_kw` 检测块 + 移除 `_bye_str` 短接块；`external_id` 格式兼容旧数据（无 `#seq` 后缀的旧工单 LIKE 查询仍能匹配）

---

### v1.3 · 2026-07-21

- **变更类型**：流程逻辑变更 + Prompt 调整
- **变更内容**：
  - 修复 `run_cleanup` 前缀误删 bug：`cheduan` 前缀匹配到 `cheduan_manual` 导致活跃集合被清理
  - 工单状态统一为英文：`pending_dispatch` → `pending`（对应 `dispatched` / `in_progress` / `resolved` / `closed`）
  - `submit()` 新增派单日志输出（服务端可见，含被分派人、置信度、决策类型、理由摘要）
  - 工单数据库连接迁移至 `ai/core/database.py`，不再依赖 `backend/app`
- **变更原因**：
  - cleanup 误删导致车端错误码集合入库后立即被清空，检索无结果
  - 英文状态便于前后端统一，避免中英混杂
  - 派单过程此前静默执行，团队无法确认自动派单是否生效
- **效果对比**：待观察
- **回滚方式**：`git revert` cleanup 修复 commit；DB 连接回退到 `app.core.database` 导入

---

### v1.2 · 2026-07-20

- **变更类型**：架构迁移 + 新增子模块
- **变更内容**：
  - Assigner（智能派单）子模块接入：`submit()` 生成工单后自动调用四层派单流水线
  - 派单流水线：规则过滤 → 多路召回（关键词 + 语义）→ LLM 综合分析 → 规则回退
  - 派单结果注入工单返回（`assignee` / `assign_confidence` / `assign_reasoning` / `assign_decision_type`）
  - 历史工单列表接口 `/api/ai/memory/tickets/all`（分页 + 按状态/类型筛选）
  - Assigner 从旧 `basic_function/` 迁移至 `ai/agents/AiDiagnosisPlatform/assigner/`，全异步化重构
- **变更原因**：工单生成后需自动推荐负责人，减少人工派单环节
- **效果对比**：3 种工单场景各分派给正确工程师（confidence=0.85，decision_type=auto）；派单失败不阻塞工单生成
- **回滚方式**：移除 `pipeline.submit()` 中 assign_ticket 调用块即可关闭自动派单

---

### v1.1 · 2026-07-19 ~ 2026-07-20

- **变更类型**：检索策略变更 + Prompt 调整 + 流程逻辑变更
- **变更内容**：
  - **检索双路策略**：错误码查询 → payload filter 精确匹配优先 + 纯向量检索仅用于无码场景
  - **LLM 幻觉修复**：检测到用户明确问某错误码但检索无结果时，在 prompt 顶部注入「未找到匹配项」标记 + 铁律规则，禁止 LLM 自行编造
  - **车端错误码知识库**：PDF 解析器 v3 重写（无表头依赖、自动识别 code 列、合并单元格支持），从 55 条提升至 171 条
  - **排查树集成**：新增 `retrieve_troubleshooting` 检索路径 + prompt 中分流/逐步骤引导规则
  - **翻译表检索**：新增 `retrieve_translation` 路径，辅助理解车端错误码英文描述
  - **Qdrant 本地文件模式**：从 Docker 切换为 RocksDB 本地模式，共享 QdrantClient 实例避免文件锁冲突
  - **知识库自动入库框架**：`BaseIngester` 通用基类 + registry 自动发现 parser + `ingest_all` 一键入库
- **变更原因**：
  - 假错误码（如 1031）被 LLM 编造答案，真错误码查不到——根因是纯向量检索对数字 embedding 稀释严重
  - 原 PDF 解析器依赖表头匹配，只能提取 55 个码，遗漏大量合并单元格中的错误码
  - 排查树 JSON（46 个故障场景）需入库并在诊断流程中逐步骤引导
- **效果对比**：
  - 错误码检索：精确匹配命中率 100%（码在库中时），幻觉率降至 0（码不在库中时明确告知未收录）
  - PDF 解析：171 条（+211%），覆盖所有 4 位和 5 位错误码
  - 排查树：模糊描述（如"车不动了"）能分流出 4 个匹配场景让用户确认
- **回滚方式**：检索双路策略在 `retrieval.py` `retrieve_cheduan()` 中，回退到纯向量模式需恢复旧逻辑；prompt 铁律在 `DIAGNOSIS_PROMPT` 模板中

---

### v1.0 · 2026-07-18（ai/ 独立部署基线）

- **变更类型**：架构迁移
- **变更内容**：
  - `ai/` 从 `backend/app/ai/` 独立为项目顶层目录，`ai/run.py` 作为 FastAPI 独立启动入口（端口 8401）
  - AI 模块完全自举，不依赖 backend 任何模块（`sys.path` 仅加项目根目录）
  - 统一路由挂载：`/api/ai/qa`（诊断 Agent）、`/api/ai/chat`（纯 LLM 对话）、`/api/ai/memory`（会话记忆）
  - 诊断 Agent 基础流水线：意图分类（howto/troubleshoot/chat）→ 知识库检索 → LLM 生成
  - 会话记忆存 Redis，支持多轮上下文
  - Embedding 模型：`bge-small-zh-v1.5`（本地 CPU 推理，512 维）
- **变更原因**：团队架构重构，AI 模块独立部署，与 backend（8400）/ frontend 并列
- **效果对比**：独立部署启动成功，三条主链路（ask / ask stream / chat stream）可跑通
- **回滚方式**：`git checkout 4ee47ad~1` 回退到 migration 前，AI 代码仍在 `backend/app/ai/` 下运行

---

## Assigner 子模块（智能派单）

### v1.0 · 2026-07-20

- **变更类型**：架构迁移 + 全异步化重构
- **变更内容**：
  - 从 `backend/app/ai/basic_function/assigner/` 迁移到 `ai/agents/AiDiagnosisPlatform/assigner/`
  - 移除旧 callback/依赖注入模式（`llm_client_callback` / `llm_async_callback` / `embed_client`），改为直接调用 `ai.core`（LLM / Embedding 单例）
  - 移除同步方法（`assign()` / `decide()` / `recall()`），仅保留异步 `aassign()`
  - 新增 `assign_ticket()` / `load_engineers()` 便捷入口（`__init__.py` 导出）
  - 修复缺失的 `ai/utils/keywords.py` 工具
  - 修复 `ai/core/embed.py` 本地模型路径探测（HuggingFace 模型名 → `embed_models/` 目录）
  - 敏感数据脱敏：`engineers.json` / `task_matching.json` 仅 `.example` 模板提交，真实文件 `.gitignore` 排除
- **变更原因**：团队架构重构——`ai/` 从 `backend/app/ai/` 独立为顶层目录
- **效果对比**：四层流水线全部验证通过（规则过滤 → 多路召回 → LLM 决策 → 规则回退）；3 种不同工单场景各分配给正确工程师（confidence=0.85，decision_type=auto）
- **回滚方式**：`git revert` 对应 commits（assigner 子模块迁移 + `pipeline.submit()` 接入点）
