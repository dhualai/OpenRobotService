# @# 跨工单引用能力 —— 设计与实施

> 日期：2026-08-16 | 状态：分两次实施，第一次（Phase 1+2 最小闭环）已完成
> 定位：AiTaskPlatform 工单讨论区支持 `@#编号` 引用另一个工单的上下文，辅助当前诊断。（本轮只做 AiTaskPlatform，不预留 AiDiagnosisPlatform 复用）

## 一、交互语法：`@` 与 `@#` 分离命名空间

| 语法 | 语义 | 说明 |
|------|------|------|
| `@` | 人员推荐 | 现状心智，保持不动 |
| `@#` | 工单引用 | 默认按"当前工单"(标题+描述) 相似检索弹列表 |
| `@#44123` | 明确引用编号工单 | 不弹列表，直接引用该工单上下文 |

- 网页端输入 `@` 但没跟 `#` 时，输入框 placeholder 提示"输入 @#工单号 引用历史工单"（已加）。
- 推荐范围：按标题+描述相似；允许跨项目；现阶段不做权限过滤。
- 两条路都支持：① `@#` 弹相似列表选（找相似，Phase 0 实现）② `@#44123` 直接写编号（明确引用，本轮已通）。

## 二、联动语义

1. **新加入上下文**：被引用工单按 **L2 深度**注入。内容来源按"能否可靠落库"划分：
   - ✅ 可靠落库：基本信息（title/description/status/车型/故障码）+ 提单 Agent 交付的 diagnosis 摘要（problem_summary/hypotheses，存于 `metadata_info.diagnosis`）+ 该工单讨论区评论（`load_discussion`，discuss 内容已落库）+ **最终解决方案 solution**（单独字段，存于 `metadata_info.diagnosis.solution`，最有参考价值）。
   - ❌ 不入库、拿不到：**任务 Agent「帮我分析」生成的完整诊断报告**（diagnose 即时生成不落库）。故不注入，也让 LLM 不要假装看到了它。
   - 附件摘要按需，原始日志默认不重读。
2. **跳转入口**：讨论区回复/评论里 `@#44123` 渲染成可点击链接 → 跳转到该工单详情页 `/tasks/{id}`（本轮已通）。

> **关键事实澄清（2026-08-16）**：两个"diagnosis"不是一回事——诊断 Agent 提单时交付的 diagnosis 摘要（落库）≠ 任务 Agent「帮我分析」的诊断报告（不落库）。引用历史工单能拿到前者 + solution + 讨论评论，拿不到后者。

## 三、实现形态

- **预加载**：`@#` 引用在 `discuss()` 入口解析，拼进 `DISCUSS_USER_TEMPLATE` 的 `referenced_tickets` 段（不走 Supervisor 主动调度）。
- **无状态**：每次请求现解析现拉取，不额外存结构化引用边。

## 四、相似检索落点（Phase 0，第二批次）

- 新增轻量后端接口 `GET /api/tasks/{task_id}/similar`，返回相似工单 `[{task_id, title, status}]`。
- 第一版用 **SQL 标题/描述关键词**相似（轻、可限定 resolved）；Qdrant 向量留作后续优化。
- **现实约束**：`task_resolutions` 只在工程师确认方案时回写，索引的是【已解决工单】。已接受限定：`@#` 相似工单第一版限定在已解决的相似工单。

## 五、实施状态

### 已完成（第一次：Phase 1 + Phase 2 最小闭环）
| 层 | 文件 | 改动 |
|----|------|------|
| 后端上下文 | `ai/agents/AiTaskPlatform/contexts/contexts.py` | 新增 `extract_referenced_task_ids` / `load_referenced_task_context` / `format_referenced_tickets` / `_format_solution`（L2 注入组装，含 solution + 讨论评论） |
| 后端模型 | `ai/agents/AiTaskPlatform/schemas.py` | `TaskContext` 新增 `solution` 字段（`metadata_info.diagnosis.solution`） |
| 后端讨论 | `ai/agents/AiTaskPlatform/handlers/discuss_flow.py` | `discuss()` 入口解析 `@#编号`，注入 `referenced_tickets` 到 prompt |
| 后端 Prompt | `ai/agents/AiTaskPlatform/prompts/prompts.py` | `DISCUSS_USER_TEMPLATE` 新增 `{referenced_tickets}` 占位 |
| 前端渲染 | `frontend/src/shared/components/MarkdownRenderer.tsx` | `preprocessTicketRef`：`@#44123` → 可点击链接 `/tasks/{id}` |
| 前端提示 | `frontend/src/shared/components/DiscussionPanel.tsx` | placeholder 增加 `@#工单号` 提示 |
| 后端通知 | `backend/app/modules/tasks/api/task.py` | `_maybe_notify_mentions` 正则排除 `@#` 工单引用（`@(?!\d)`） |

### 待实施（仅剩 Phase 3）
- Phase 3：Supervisor 能力表可选加 `ticket_ref` 只读观察/兜底能力（可选，非核心）。

### 已完成（第二次：Phase 0）
| 层 | 文件 | 改动 |
|----|------|------|
| 后端接口 | `backend/app/modules/tasks/api/task.py` | 新增 `GET /{task_id}/similar`：按当前工单标题+描述关键词打分检索**已解决**相似工单（标题+3/描述+1），跨项目、无权限过滤、排除自身，返回 `{task_id, title, status, project_name}` |
| 前端 | `frontend/src/shared/components/DiscussionPanel.tsx` | `@#` 触发拉相似工单下拉（`fetchSimilarTickets` 调 `/api/tasks/{taskId}/similar`）；选中替换为 `@#编号`；`@#44123` 直接写编号不弹；键盘上下/Enter/Escape；无触发符自动收起面板 |

## 六、使用示例

用户在当前工单讨论区输入：
```
@#44123 你看一下这个工单当时是怎么解决的，能参考吗？
```

后端 `discuss()`：
- `extract_referenced_task_ids` 解析出 `["44123"]`
- `format_referenced_tickets` 组装 L2 上下文（标题/描述/状态/车型/故障码/diagnosis）
- 拼进 `DISCUSS_USER_TEMPLATE` 的 `referenced_tickets` 段，LLM 可结合它回复

前端渲染：AI 回复里的 `@#44123` → 点击跳转 `/tasks/44123`。
