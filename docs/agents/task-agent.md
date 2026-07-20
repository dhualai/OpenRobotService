# 任务 Agent 版本迭代记录

> 对应 Agent：AiTaskPlatform（任务 Agent — 系统任务 / 供给视角）
> Owner：AI 算法工程师
> 设计文档：`ai/agents/AiTaskPlatform/TASK_AGENT_DESIGN.md`

---

### v1.0 · 2026-07-20

- **变更类型**：初始版本 — 完整 Agent 实现
- **变更内容**：
  - 新建 `ai/agents/AiTaskPlatform/` 模块（7 个源文件）
  - 实现 7 个 API 端点：`chat/stream`（自由问答）、`analyze/stream`（工单分析→方案草稿）、`submit`（方案提交）、`list`（工单列表）、`health`
  - 前端集成：ChatPanel 场景切换（tasks 场景→任务 Agent API）、Message 扩展 solution_draft、SolutionCard 可编辑组件、taskId 变化时自动注入诊断上下文
  - LLM Prompt：`TASK_CHAT_SYSTEM_PROMPT`（自由问答）+ `TASK_AGENT_SYSTEM_PROMPT`（工单分析，含四条铁律：不复诊、不排除 ruled_out、不追问已收集信息、不编造）
  - 数据：直接从 `tickets` 表读工单（SQLAlchemy），不调后端 API；submit 写回 tickets 表
  - 上下文：同一 session_id 贯穿 chat/analyze，Redis memory 共享对话历史
- **变更原因**：产品需求 — 系统任务视角需要 AI 辅助工程师处理工单
- **效果对比**：Mock 数据验证 confidence=0.92，4 步可执行方案；15 项单元测试全部通过。Qdrant 排查树 + 历史工单方案就绪后会进一步提升
- **回滚方式**：`git revert` 从 `feature/task-agent` 合并的 commits；前端回退 ChatPanel.tsx/TasksView.tsx 到上一版
