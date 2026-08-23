# 提单/知识问答 Agent 版本迭代记录

> 对应 Agent：AiDiagnosisPlatform（提单 Agent — 我要摇人 / 需求视角）
> Owner：知识库/问答 Agent 工程师
> 
> **本文档中「Assigner 子模块」章节由任务 Agent 工程师维护**（Assigner 于 2026-07-20 从旧 `basic_function/` 迁移至 `ai/agents/AiDiagnosisPlatform/assigner/`，归属提单 Agent，实际维护由任务 Agent 工程师负责）。

---

## 提单 Agent 主线

### v1.6 · 2026-07-28 ~ 2026-07-29

- **变更类型**：流程逻辑变更 + 闭环保护 + 修复 + 接口变更
- **变更内容**：
  - **闭环保护（防重复提单）**：新增 `_can_submit(state)` 函数，在 `_agent_think` / `_agent_think_stream` / `prepare_ticket`（按钮路径）三处统一拦截——已提交（resolved）且无新问题描述时拒绝再次提单。`_apply_state_update` 移至 `_can_submit` 之前执行，优先用 LLM 提炼后的 `problem_summary` 判断内容有效性，避免字数/关键词等简单规则误判。
  - **escalated 不再无条件拦截**：`escalated` 与 `resolved` 统一逻辑——有新 `problem_summary` 即放行（允许处理中新问题另起工单），仅空问题才拦截。`run()` / `run_stream()` 中 phase 重置列表同步加上 `"escalated"`。
  - **提单人（created_by）修复**：所有提单路径（`/ask`、`/ask/stream`、`/ticket/confirm`、`/upload`）统一从 `Authorization: Bearer <JWT>` 解析 `payload["sub"]` → `username`，传入 `DiagnosisRequest.created_by` → `upsert_task(created_by=username)`。不再使用 `debug_test_user` 或 `system` 硬编码。
  - **上传附件重构**：
    - **不再污染对话上下文**：删除上传后虚假 `add_turn("user", ...)`（此前 `[上传了附件] jpg_8805.jpg` 会被 LLM 误读为错误码 8805 去知识库检索）。附件列表存入 `agent_state.attachments`，VLM 图片描述存入 `collected_info.image_description`，诊断 prompt 自然展示。
    - **单传附件回复**：只传文件时后端生成确认回执（`ack_message`），以 **assistant turn**（非 user turn）写入对话。图片经 VLM 分析后直接输出初步诊断（prompt 从"客观描述不下结论"改为"分析异常/错误码，给出初步判断"）；非图片返回"暂不支持解析除图片以外的文件类型，提单后将作为参考依据"。
    - **附件+文字一次请求**：`/upload` 新增可选 `message` 字段，传入时在上传完成后立即调用 `pipeline.run()`，VLM 图片分析作为 `collected_info` 参考上下文随 prompt 进入诊断，返回 `ai_response`（含 message/action/thinking/ticket）。
  - **测试覆盖**：新增 75 个单元测试（`test_can_submit.py` 65 个 + `test_upload_no_pollution.py` 10 个），覆盖闭环保护全路径（对话/按钮/混合）、LLM 提炼 chitchat 拦截、必填字段校验、上传不污染对话、附件元数据正确落盘。
- **变更原因**：
  - 产品要求：用户提交工单后再点按钮/说"转工单"不能重复生成工单，两条路径都要闭环
  - 提单人无法追溯——前端此前未传 Bearer token 导致所有工单创建者显示 `debug_test_user`
  - 上传 `jpg_8805.jpg` → LLM 看到 `8805` → 当成错误码在知识库中搜索 → 搜不到开始胡说
  - 产品要求上传附件有回复（"能解析的立即解析解答，不能解析的说给接单人参考"），且支持上传同时附带文字
- **效果对比**：
  - 闭环：resolved/escalated + 空问题 → 拦截；有新故障描述 → 放行；LLM 判断"好的谢谢"为废话 → 清空 problem → 拦截
  - 提单人：有 token → `hu_jiannan`；无 token → 空字符串（不影响功能）
  - 上传：文件名不再出现在 LLM 上下文；图片上传返回 VLM 初步分析；附件+文字一次请求完成上传+诊断
  - 75 个测试全部通过，零回归
- **回滚方式**：`git revert a4122c0`；或单独回退：`_can_submit` 函数改为 `return True, ""` 关闭闭环；`/upload` 恢复旧的 `add_turn` 逻辑

---

### v1.5 · 2026-07-24

- **变更类型**：新功能 + 修复 + 仓库清理
- **变更内容**：
  - **对话标题生成**：第 2 轮对话结束后用 LLM 生成会话标题，通过独立 SSE `event: title` 发送给前端。中文不超过 15 字，英文不超过 50 字符，`max_tokens=40`。标题同时写入 `memory.metadata["title"]`，后续轮次不再重新生成。`/ask/stream` 端点新增 `title` event type 处理。
  - **工单状态修正**：新提 AI 工单状态从 `PENDING`（待处理）改为 `NEW`（新建），`ticket_dict_to_task_fields()` 中 `status` 字段变更，`AssignerWorker._get_pending_tickets()` 同步改为查询 `status='new'`。
  - **Git 仓库清理**：移除本地生成/敏感数据出库——
    - `ai/kb/qdrant/` 及 `ai/kb/.ingest_state/`（18 个文件，~12MB）不再跟踪，加入 `.gitignore`
    - `ai/integrations/wecom/user_map.json`（含 23 人真实姓名+邮箱）移除跟踪，新增 `user_map.example.json` 模板，加入 `.gitignore`
  - **图片路径修复**（`operation_prose_docx.py`）：修复入库时 image URL 拼接 double `media/` bug，content body 中 pandoc 生成的相对路径 `](media/xxx.png)` 替换为完整 URL。
  - **Embedding 模型外迁**：`EMBEDDING_MODEL_NAME` 改为绝对路径 `D:\Code\OpenRobotService_Data\embed_models\...`，模型文件不再随项目代码上传。
- **变更原因**：
  - 前端需要在对话列表展示会话标题，前后端约定通过独立 SSE event 传递
  - AI 提的单子应该是「新建」而非「待处理」，否则统计数据不准、派单 Worker 查询条件不一致
  - 之前 `ai/kb/` 整个 Qdrant 数据目录和 PII 数据被跟踪，随代码推到 GitHub 有隐私泄漏风险且污染仓库
- **效果对比**：标题在 Round 2 结束时正确生成并发出（测试：`event: title` → `{"title": "AGV充电桩指示灯不亮排查"}`）；新工单状态 `new` 覆盖写入+派单扫描路径；Git 跟踪文件减少 19 个。
- **回滚方式**：标题生成在 `pipeline.py` `_generate_title()` 中，注释掉调用块即可关闭；工单状态改回 `TaskStatus.PENDING` 恢复旧行为。

---

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
