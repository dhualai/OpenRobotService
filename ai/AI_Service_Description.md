# AI 服务接口文档（AI Service Description）

> 适用模块：`ai/`（问答 / 对话 / 记忆 / 任务 Agent / 企业微信 / 数据分析）
> 后端入口：`ai/run.py`（FastAPI，挂载于 `/api/ai/*`）
> 更新日期：2026-08-15（与 `ai/api/router.py` 及 `ai/run.py` 对齐）

---

## 1. 服务总览

AI 服务以 FastAPI 提供一组 REST / SSE 接口，按功能前缀划分：

| 前缀 | 功能 | 说明 |
|------|------|------|
| `/api/ai/qa/*` | 诊断问答 Agent | 统一问答、流式输出、工单生成与确认、附件上传、健康检查 |
| `/api/ai/chat/*` | 纯 LLM 对话 | 不带诊断流程的文字对话（非流式 / 流式） |
| `/api/ai/memory/*` | 会话记忆 | 对话历史、待派单列表、历史工单、清除会话 |
| `/api/ai/task/*` | 任务 Agent | 诊断报告、讨论、摘要、提交方案、健康检查 |
| `/api/ai/wecom/*` | 企业微信集成 | 项目拉取 / 分页查询 / 更新 |
| `/api/ai/analysis/*` | 数据分析平台 | 数据分析、快速对话、分析类型、报告生成 |

> 说明：原文档中的「智能派单（/api/ai/ticketReferee）」独立路由及 `assigner_router` 已被移除。
> 派单相关能力现并入诊断 Agent（`/api/ai/qa/*`）的工单流程与 `memory` 的待派单列表，
> 不再以独立接口对外暴露。

### 1.1 鉴权

- 浏览器请求携带登录态（Cookie / 网关注入的 user header）。
- 关键写接口（如 `/api/ai/qa/ask`、`/api/ai/qa/ask/stream`、`/api/ai/qa/ticket/confirm`）
  要求有效登录态，缺失时返回 `401` 触发前端 `fetchWithAuth` 刷新重试。
- 企业微信接口在 `WECOM_CORPID / WECOM_CORPSECRET` 未配置时整体不可用。

### 1.2 通用返回结构

非流式 JSON 接口统一返回：

```json
{ "code": 0, "message": "", "data": { } }
```

- `code = 0` 成功；`code = 1` 业务/系统错误，`message` 含原因。

---

## 2. 诊断问答 Agent（`/api/ai/qa`）

### 2.1 `POST /api/ai/qa/ask` — 统一问答（非流式）

请求体（`QAAskRequest`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话 ID（必填） |
| `query` | string | 用户问题（必填） |
| `skip_retrieval` | bool | 跳过知识库检索，默认 false |

响应：诊断流程结果（含 `code`、诊断结论、追问/补信息状态、工单草稿等）。

### 2.2 `POST /api/ai/qa/ask/stream` — 流式问答（SSE）

请求体同 `QAAskRequest`，额外可选：

| 字段 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | string | 传入时启用后端增量落库（建占位 assistant 消息） |
| `assistant_message_id` | string | 指定增量落库的 assistant 消息 ID |

SSE 事件：

| event | data | 含义 |
|-------|------|------|
| `message_created` | `{message_id}` | 占位 assistant 消息已建（仅 `conversation_id` 时） |
| `first_token` | `{ms}` | 首 token 延迟（毫秒） |
| `token`（裸 data） | `{token}` | 流式文本片段 |
| `status` | `{stage, round, ...}` | 阶段（如 diagnosing / need_info / need_fields / review） |
| `result` | 结果对象 | 诊断结果 / 工单草稿 |
| `title` | `{...}` | 生成的标题 |
| `memory_written` | `{ack_message}` | 附件已写记忆的回执话术 |
| `file_saved` | `{saved, filenames}` | 附件落库完成 |
| `vision_start` | `{names}` | 多模态视觉理解开始 |
| `vision_done` | `{desc}` | 视觉理解结论 |
| `vision_error` | `{error}` | 视觉理解失败 |
| `error` | `{error}` | 错误 |
| `done` | `{total_ms}` | 流结束 |

心跳：长时间无产出时下发 SSE 注释行 `: ping` 保活（前端只解析 `event:/data:` 行，对其透明）。

### 2.3 `POST /api/ai/qa/submit` — 生成工单并存库

请求体 `QASubmitRequest`。返回工单入库结果。

### 2.4 `GET /api/ai/qa/ticket` — 获取工单数据

查询参数：`session_id`。返回会话关联的工单数据。

### 2.5 `POST /api/ai/qa/ticket/ack` — 派单确认回执

请求体 `TicketAckRequest`：`session_id`、`dispatch_id`、`status`。
作用：从待派单列表移除该会话，并将派单结果写入记忆 `metadata.agent_state.dispatch`。

响应：`{code:0, data:{session_id, message:"已确认"}}`

### 2.6 `POST /api/ai/qa/ticket/prepare` — 生成工单草稿（路径 1：按钮转工单）

请求体 `QASubmitRequest`（`session_id`）。调用 `pipeline.prepare_ticket` 生成草稿。

### 2.7 `POST /api/ai/qa/ticket/confirm` — 确认提交工单（路径 1：弹窗确认后）

请求体 `TicketConfirmRequest`：`session_id`、`overrides`、`username`（兜底）。
要求登录态；调用 `pipeline.confirm_submit` 提交工单。

### 2.8 `GET /api/ai/qa/ticket/draft` — 获取待确认草稿（轮询兜底）

查询参数：`session_id`。返回待确认工单草稿。

### 2.9 `DELETE /api/ai/qa/ticket/draft` — 取消确认：清除待确认草稿

查询参数：`session_id`。用户关闭/放弃提单时调用。

### 2.10 `POST /api/ai/qa/upload` — 上传附件

`multipart/form-data`：`session_id`、`files`（多文件）。
附件上传至 **MinIO**（桶取自 AI 配置 `minio_bucket`），返回预签名 URL；图片类附件会做视觉理解并回写记忆。

### 2.11 `GET /api/ai/qa/health` — 健康检查

返回服务健康状态。

---

## 3. 纯 LLM 对话（`/api/ai/chat`）

### 3.1 `POST /api/ai/chat` — LLM 对话（非流式）

请求体含 `session_id`、`query` 等；直接调用 LLM 返回文本。

### 3.2 `POST /api/ai/chat/stream` — LLM 对话（流式 SSE）

SSE 事件：`first_token`、`token`（裸 data）、`done`、`error`。

---

## 4. 会话记忆（`/api/ai/memory`）

### 4.1 `GET /api/ai/memory/history` — 查看对话历史

查询参数：`session_id`。返回会话多轮对话。

### 4.2 `GET /api/ai/memory/tickets` — 待派单列表

返回当前待人工派单的会话列表（由诊断 Agent 标记为需派单的会话）。

### 4.3 `GET /api/ai/memory/tickets/all` — 历史工单列表

返回已生成的历史工单。

### 4.4 `DELETE /api/ai/memory/clear-all` — 清除所有会话

### 4.5 `DELETE /api/ai/memory/clear` — 清除对话历史

查询参数：`session_id`。

---

## 5. 任务 Agent（`/api/ai/task`）

### 5.1 `POST /api/ai/task/diagnose` — 诊断报告（[帮我分析] 按钮）

请求体含 `session_id`、上下文；返回结构化诊断报告。

### 5.2 `POST /api/ai/task/discuss` — @U老师 讨论

针对诊断结论发起讨论。

### 5.3 `POST /api/ai/task/summarize` — 讨论摘要

### 5.4 `POST /api/ai/task/submit` — 提交方案

### 5.5 `GET /api/ai/task/health` — 健康检查

---

## 6. 企业微信集成（`/api/ai/wecom`）

需要配置 `WECOM_CORPID / WECOM_CORPSECRET / WECOM_DOCID / WECOM_SHEET_ID`。

### 6.1 `GET /api/ai/wecom/projects` — 拉取全部项目

### 6.2 `GET /api/ai/wecom/projects/search` — 分页查询项目

### 6.3 `POST /api/ai/wecom/projects/{record_id}` — 更新单条项目

### 6.4 `GET /api/ai/wecom/health` — 健康检查

---

## 7. 数据分析平台（`/api/ai/analysis`）

由 `ai/agents/AiDataAnalysisPlatform/router.py` 提供，挂载前缀 `/api/ai/analysis`。

### 7.1 `GET /api/ai/analysis/health` — 健康检查

### 7.2 `POST /api/ai/analysis/analyze` — 数据分析

### 7.3 `POST /api/ai/analysis/chat` — 快速对话

支持四种模式：

- 自动判别：仅传 `question` / `context`，后端按问题语义自动判断是聊天还是分析
- 数据分析聊天：额外传 `data`，并可指定 `data_source`、`analysis_type`
- 自动查库分析：不传 `data`，改传 `project_code` 或 `user_id`，并可指定 `period`、`date`、`analysis_type`
- 页面上下文补全：不显式传分析范围时，可传 `context_meta` 让后端补齐项目/周期/日期

请求示例 1：纯聊天

```bash
curl -X POST "http://localhost:8000/api/ai/analysis/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "请概括一下这个平台的用途",
    "context": "面向自动化测试和项目数据分析"
  }'
```

说明：

- 若只传 `question` / `context`，后端会先做意图识别
- 识别为普通问答：返回 `mode = "chat"`
- 识别为数据分析时，后端会优先使用显式参数；缺失项再尝试从 `context_meta` 补全
- 若仍未传 `data`、`project_code`、`user_id`：返回 `400`，提示补充分析数据或查询范围

请求示例 2：带数据分析

```bash
curl -X POST "http://localhost:8000/api/ai/analysis/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "请帮我分析异常任务的主要问题，并给出处理建议",
    "context": "这是今天的任务执行数据",
    "data": "[{\"robot_id\":\"R001\",\"status\":\"failed\",\"duration\":125,\"reason\":\"导航超时\"},{\"robot_id\":\"R002\",\"status\":\"success\",\"duration\":45,\"reason\":\"\"},{\"robot_id\":\"R003\",\"status\":\"failed\",\"duration\":98,\"reason\":\"接口返回500\"}]",
    "data_source": "json",
    "analysis_type": "task_stats"
  }'
```

请求示例 3：自动查库分析

```bash
curl -X POST "http://localhost:8000/api/ai/analysis/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "请分析这个项目最近一周的风险和工单情况，并给出优先处理建议",
    "project_code": "PJT-001",
    "period": "weekly",
    "date": "2026-09-03",
    "analysis_type": "risk"
  }'
```

请求示例 4：仅传问题 + 页面上下文补全

```bash
curl -X POST "http://localhost:8000/api/ai/analysis/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "请分析这个项目最近一周的风险和工单情况，并给出优先处理建议",
    "context_meta": {
      "scene": "project_detail",
      "project_code": "Leo_test",
      "project_name": "摇人吧服务号",
      "period": "weekly",
      "date": "2026-09-03",
      "analysis_type": "risk"
    }
  }'
```

响应示例：纯聊天

```json
{
  "answer": "这个平台主要用于自动化测试管理、AI 问答和数据分析。",
  "mode": "chat",
  "model": "deepseek-chat",
  "usage": null,
  "analysis": null
}
```

响应示例：带数据分析

```json
{
  "answer": "## 摘要\n失败任务主要集中在导航超时和接口异常两类问题，说明当前链路同时存在执行环境稳定性和后端服务可用性问题。\n\n### 异常任务概览\n失败任务占比偏高，且平均耗时高于成功任务。\n\n## 行动建议\n1. 优先排查导航超时场景的地图与网络环境。\n2. 对接口 500 错误补充监控与重试策略。",
  "mode": "analysis",
  "model": "deepseek-chat",
  "usage": null,
  "analysis": {
    "analysis_type": "task_stats",
    "summary": "失败任务主要集中在导航超时和接口异常两类问题。",
    "insights": [
      {
        "category": "异常任务概览",
        "content": "失败任务占比偏高，且平均耗时高于成功任务。",
        "severity": "warning"
      }
    ],
    "recommendations": [
      "优先排查导航超时场景的地图与网络环境。",
      "对接口 500 错误补充监控与重试策略。"
    ],
    "raw_response": "## 摘要 ...",
    "model": "deepseek-chat",
    "usage": null
  }
}
```

响应示例：自动查库分析

```json
{
  "answer": "## 摘要\n本周项目风险主要集中在高等级未关闭风险，且存在逾期工单，需要优先处理高风险项与阻塞性问题。\n\n### 风险概览\n高等级风险占比较高，部分风险责任人负载集中。\n\n### 工单表现\n逾期工单主要分布在处理中状态，说明推进节奏存在堵点。\n\n## 行动建议\n1. 先清理高等级且未关闭的风险项。\n2. 对逾期工单按责任人和项目阶段重新排序处理优先级。",
  "mode": "analysis",
  "model": "deepseek-chat",
  "usage": null,
  "analysis": {
    "analysis_type": "risk",
    "summary": "本周项目风险主要集中在高等级未关闭风险，且存在逾期工单。",
    "insights": [
      {
        "category": "风险概览",
        "content": "高等级风险占比较高，部分风险责任人负载集中。",
        "severity": "warning"
      },
      {
        "category": "工单表现",
        "content": "逾期工单主要分布在处理中状态，说明推进节奏存在堵点。",
        "severity": "warning"
      }
    ],
    "recommendations": [
      "先清理高等级且未关闭的风险项。",
      "对逾期工单按责任人和项目阶段重新排序处理优先级。"
    ],
    "raw_response": "## 摘要 ...",
    "model": "deepseek-chat",
    "usage": null
  }
}
```

### 7.4 `GET /api/ai/analysis/types` — 分析类型列表

### 7.5 `POST /api/ai/analysis/report/generate` — 生成日报/周报

### 7.6 `GET /api/ai/analysis/report/health` — 报告服务健康检查

---

## 8. 前端调用约定

- 所有需登录态的接口使用 `fetchWithAuth` 封装，遇到 `401` 自动刷新登录态后重试。
- 流式接口使用 `EventSource` / `fetch` + 流式读取解析 SSE（`event:` / `data:` 行）。
- 附件上传复用 `/api/ai/qa/upload`，返回的 MinIO 预签名 URL 用于前端展示与后续引用。

---

## 9. 备注：已移除接口

以下接口在重构中已被移除，调用方不应再依赖：

- `POST /api/ai/ticketReferee/*`（原「智能派单」独立路由 / `assigner_router`）
- 文档旧版中独立的「数据分析平台」章节路径描述（现统一为 `/api/ai/analysis/*`）
