# 项目工单卡 API（项目详情页「项目工单」）

路由前缀 `/project-tickets`，挂在 `admin_router`（`/api/admin`）下，实际路径
`/api/admin/project-tickets/projects/{project_id}/...`。

前端页面：后台管理 → 项目详情页 →「项目工单」卡（位于「项目信息管理」与「项目动态」之间），
组件 `frontend/src/pages/admin/ProjectTicketsCard.tsx`，数据层 `frontend/src/api/projectTickets.ts`。

数据源：系统任务模块 `tasks` 表（`app.models.task.Task`，`Task.project_id` = 项目ID/代码）。
与「工单状态监测」页（`/api/admin/tickets/stats`，AI 服务 tickets 表）是两个数据源，勿混。

## 1. 卡片三部分与取数口径

1. **顶部三格汇总（总工单数 / 正在处理 / 已完成）**：复用仪表盘
   `task_dashboard_service.get_ticket_summary` 的单项目口径——总数 = 监控中的六种状态
   （new / in_progress / paused / resolved / closed / cancelled，后者由 TaskStatus.PENDING /
   CANCELED 回映射）计数之和；**正在处理 = 处理中 + 暂停/挂起**（后端 `pending_count`，
   即仪表盘「待处理」口径，新建尚未开始处理不计入）；**已完成 = 已解决 + 已关闭**
   （已取消不算完成，只在总数中体现）。接口返回完整 `by_status`，前端据此派生三格数字。
2. **核心阻滞工单**：
   - 未配置：默认规则——未完成工单（new / in_progress / pending）按
     「优先级（紧急→低）> 截止时间早者在前（无截止置后）> 创建时间新者在前」取前 3；
   - 已配置：按 AI 判定结果（`project_blocking_config.ai_result` 的工单ID 列表）回查展示，
     工单已删除或已改绑其它项目的静默剔除；列表全空时退回默认规则；
   - 条目交互：整块可点，跳转该工单详情页 `/tasks/:id`（与仪表盘「工单明细」
     TicketStatusDetail 列表同交互；PC 微信内经 navigateInWechat 转整页跳转）。
3. **工单变化趋势**：按自然周（周一为起点）统计该项目每周**新建**工单数，近 8 周（含本周）。

## 2. GET /projects/{project_id}/overview —— 工单概览

响应（`{code:0, data:...}` 信封，与仪表盘接口同款）：

```json
{
  "code": 0,
  "data": {
    "total": 10,
    "by_status": {"new": 1, "in_progress": 2, "paused": 1, "resolved": 3, "closed": 2, "cancelled": 1},
    "pending_count": 3,
    "overdue_count": 1,
    "resolved_rate": 0.6,
    "weekly": [{"week_start": "2026-07-27", "count": 2}, "...共 8 项"],
    "blocking": {
      "mode": "ai",
      "tickets": [
        {
          "id": 12, "title": "导航不识别货架", "status": "in_progress", "priority": "urgent",
          "ticket_type": "bug", "created_by": "zhang", "creator_name": "张三",
          "assigned_to": "u1", "assignee_name": "李四",
          "created_at": "2026-09-01T10:00:00", "deadline_at": "2026-09-10T00:00:00",
          "overdue": true, "description": "现场多台车复现…（80 字摘要）"
        }
      ],
      "prompt": "优先挑选影响现场验收的工单",
      "summary": "当前阻滞集中在导航识别",
      "reasons": {"12": "阻塞现场验收"},
      "updated_by_name": "管理员",
      "updated_at": "2026-09-16 12:00:00"
    }
  }
}
```

- `by_status` 为前端状态 key 口径（与仪表盘 `TICKET_STATUS_LIST` 一致），零计数状态也返回 0；
  卡片顶部三格由它派生：正在处理 = in_progress + paused，已完成 = resolved + closed；
- `blocking.mode`：`ai`（已配置）/ `default`（默认规则）；`default` 模式下若存在旧配置，
  `prompt` 仍会带回，供管理员在既有要求上修改；
- `tickets` 条目的 `status`/`priority`/`ticket_type` 是**原始枚举值**（前端自行映射标签），
  `overdue` 由服务端按 deadline 与当前时间判定（前端不再比较时间）。

## 3. POST /projects/{project_id}/blocking-config —— 配置阻滞权重（AI）

**仅管理员及超级管理员**（依赖 `get_current_admin_user`，与 `/info-nodes/template` 同一判据；
前端按钮同样按 `permissions` 含 `admin` 显隐）。

请求体：`{"prompt": "优先挑选影响现场验收、客户多次催办的工单…"}`（提示词必填）。

流程：后端取项目基础字段 + 该项目最近 60 条工单基础数据（标题/状态/优先级/类型/时间/
超期标记/提单人/接单人/描述截断 200 字）+ 管理员提示词 → 大模型输出严格 JSON
（`ticket_ids` 最多 5 个按重要性排序 + `summary` 总述 + `reasons` 逐单理由）→
过滤到候选工单 ID 内 → 覆盖写 `project_blocking_config`（每项目一行）→ 返回更新后的
`blocking` 板块（结构与 overview 内一致，前端直接替换展示）。

大模型接线与「AI 项目摘要」共用同一客户端（`backend/.env` 的 `LLM_API_KEY`，
仓库根 `ai/core/llm.py` 的 `LLMClient`，默认 DeepSeek flash）；
`LLM_READ_TIMEOUT`（ai/.env，默认 30s）即单次判定读超时。

错误约定：

| 状态码 | 场景 |
| --- | --- |
| 400 | 提示词为空；该项目暂无工单 |
| 401 | 无 / 无效 token |
| 403 | 已登录但无 admin 权限（非管理员） |
| 404 | 项目不存在 |
| 503 | AI 未配置（LLM_API_KEY 缺失）；大模型调用失败；输出无法解析或未选出有效工单 |

## 4. 新表 project_blocking_config

| 列 | 类型 | 说明 |
| --- | --- | --- |
| project_id | VARCHAR(64) PK | 项目ID（每项目一行，重新配置即覆盖） |
| prompt | TEXT NOT NULL | 管理员输入的阻滞判定提示词 |
| ai_result | TEXT NULL | AI 判定结果 JSON：`{"ticket_ids":[...],"summary":"...","reasons":{...}}` |
| updated_by | VARCHAR(64) NULL | 最后配置人登录名 |
| updated_by_name | VARCHAR(64) NULL | 最后配置人显示名 |
| updated_at | VARCHAR(30) NOT NULL | 配置时间（'YYYY-MM-DD HH:MM:SS'） |

建表方式：模型定义在 `app/models/delivery.py`（`ProjectBlockingConfig`），已注册进
`app/models/__init__.py`，后端启动时 `Base.metadata.create_all` 自动建表，无需手工脚本
（与 `project_info_template` 同约定）。**部署新版本后需重启后端**，表才会创建。

## 5. 测试与前端

- 后端：`backend/tests/test_project_tickets.py`（23 例，反射 runner，见文件头运行方式）——
  周趋势分桶、默认阻滞排序、提示词组装、AI 结果解析容错、阻滞板块取数与降级、
  描述摘要截断、空提示词校验。
- 前端：`frontend/src/pages/__tests__/ProjectTicketsCard.test.tsx`（11 例）——
  统计格、阻滞条目各字段、点击条目跳转工单详情、AI 模式徽标/总述/理由、趋势图数据、
  空态、失败重试、管理员按钮显隐、弹窗预填与提交、空提示词与失败分支。
