# 提单/知识问答 Agent 版本迭代记录

> 对应 Agent：AiDiagnosisPlatform（提单 Agent — 我要摇人 / 需求视角）
> Owner：知识库/问答 Agent 工程师
> 
> **本文档中「Assigner 子模块」章节由任务 Agent 工程师维护**（Assigner 于 2026-07-20 从旧 `basic_function/` 迁移至 `ai/agents/AiDiagnosisPlatform/assigner/`，归属提单 Agent，实际维护由任务 Agent 工程师负责）。

---

## 提单 Agent 主线

> **[待知识库/问答 Agent 工程师填写]**

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
