---
name: 修复转工单计数实时刷新（badge 红点）
overview: 修复「我要摇人」页右上角 badge 红点计数不实时更新的问题。根因：对话里输入「转工单」让 AI 自动建单时，前端 ChatPanel 解析 SSE result 事件未感知 data.ticket 字段，从不调用 refreshTasks()，导致该路径建单后 badge 不刷新。外层按钮路径已正常调用 refreshTasks()。修复方案为纯前端：在 ChatPanel.send 中检测 result.ticket 并在流式结束后触发 refreshTasks()，使两条建单路径口径一致。后端无需改动（SSE 已回传 ticket）。计数口径维持 new/in_progress/pending 不变。
todos:
  - id: chatpanel-ticket-refresh
    content: 在 ChatPanel.send 中感知 SSE result 事件的 data.ticket 标记，并在流式结束后调用 refreshTasks
    status: completed
  - id: sync-docs
    content: 通读 docs 与 frontend/backend 相关 MD，同步更新涉及 badge 或转工单计数的说明
    status: completed
    dependencies:
      - chatpanel-ticket-refresh
  - id: verify-badge
    content: 验证两条建单路径红点均 +1，并运行前端构建确认无 TS/lint 报错
    status: completed
    dependencies:
      - chatpanel-ticket-refresh
---

## 用户需求

修复「我要摇人」页右上角 badge 红点计数不实时刷新的问题：用户输入含「转工单」等关键词、由 AI 后端自动生成工单后，红点计数未能 +1。

## 产品概述

右上角历史工单红点用于提示当前用户处于活跃状态（new/in_progress/pending）的工单数量。当前外层「转工单」按钮建单后计数正常，但 AI 对话自动建单后计数不更新，导致红点数值与实际活跃工单数不一致。

## 核心功能

- 在 AI 对话自动建单路径（用户在对话中输入「转工单」等触发后端 `submit` 生成工单）结束后，触发 badge 重新计算，使红点与实际活跃工单数一致。
- 保留外层「转工单」按钮建单路径不变，其行为继续正常 +1（回归保障）。
- 计数口径维持现状：仅统计 `new / in_progress / pending`，不纳入 `dispatched`，不统一建单入口、不做去重。

## 技术栈

- 前端：React + TypeScript + Zustand（工作台状态中枢）+ tdesign-mobile-react（现有约定）
- 后端 AI 服务：FastAPI（`ai/agents/AiDiagnosisPlatform/pipeline.py` 流式诊断 + 自动建单），**无需改动**

## 实现方案

### 总体策略

纯前端单文件改动。后端 `run_stream` 在 AI 自动建单时，已通过 SSE `result` 事件回传 `data.ticket` 字段（`pipeline.py:1288` 赋值、`pipeline.py:1304` yield、`router.py:114-115` 透传）。现有 `ChatPanel.send` 解析 `result` 事件时仅判断 `data.root_cause_analysis`（任务 Agent 专属），完全忽略了 `data.ticket`，因此从不调用 `refreshTasks()`。

修复方法：在 `ChatPanel.send` 的 SSE 解析流程中感知 `data.ticket`，并在流式正常结束后调用已作用域内的 `refreshTasks()`。该调用会自增 `tasksRefreshKey`，`CallView.tsx` 的 `useEffect([tasksRefreshKey])` 随即重新拉取 `qaListTickets` 并计算 `unread`，使红点更新。外层按钮路径已调用 `refreshTasks()`，两条路径行为统一。

### 关键技术决策

- **复用现有机制而非新增接口**：`refreshTasks()` 已在 `ChatPanel` 顶部解构（`ChatPanel.tsx:71`），无需新增 import 或封装；直接复用即可，零新增 API、零后端改动，符合最小改动与 YAGNI 原则。
- **标记位而非立即刷新**：在 `while` 循环前置 `ticketCreatedThisTurn` 标志，循环内命中 `data.ticket` 时置位，循环结束（流式正常完成）后再调用 `refreshTasks()`。避免在流式未结束前多次刷新，且若流式抛错（`catch` 分支）则不触发，保持与外层按钮一致的「建单成功才刷新」语义。

### 实现注意事项

- 与现有 `data.root_cause_analysis` 判断并列新增 `if (currentEvent === 'result' && data.ticket) ticketCreatedThisTurn = true;`，互不干扰（`tasks` 场景 `data.root_cause_analysis` 不存在，本就走不到）。
- 调用点放在现有「流式结束：持久化 AI 回复」逻辑（`ChatPanel.tsx:287`）之后、`catch` 之前，确保消息落库完成且本轮无异常时才刷新。
- 不引入额外重渲染：`refreshTasks` 仅自增计数触发一次 `CallView` 数据拉取，开销可忽略。

## 架构设计

本改动不涉及架构调整，仅补齐既有「AI 自动建单 → badge 刷新」数据链的缺失环节，与外层按钮路径共用同一 `tasksRefreshKey → CallView useEffect → qaListTickets` 刷新链路。

## 目录结构

```
frontend/
└── src/
    └── shared/
        └── components/
            └── ChatPanel.tsx   # [MODIFY] 在 send() 的 SSE 解析中感知 result 事件的 data.ticket，
                                #   声明 ticketCreatedThisTurn 标记；流式正常结束后调用
                                #   refreshTasks() 触发 badge 重新计算。后端与计数口径均不变。
```

（其余文件 `CallView.tsx`、`ai/api/router.py`、`ai/agents/AiDiagnosisPlatform/pipeline.py` 无需改动，仅作为链路依据。）