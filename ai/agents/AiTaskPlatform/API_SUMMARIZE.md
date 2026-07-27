# 讨论摘要 API 协议

> 服务：任务 Agent（AI 服务 8401）
> 端点：`POST /api/ai/task/summarize`
> 版本：v3.1

---

## 概述

后端定时触发 → AI 模块自动扫描所有活跃工单 → 逐条判断是否需生成摘要 → 生成则写入 `task_comments`（📝 讨论摘要）。

**后端只需触发，不需要传任何参数**。AI 模块自己读 DB、自己判断、自己写结果。

---

## 请求

```json
POST /api/ai/task/summarize

{} // 空 body，或任意占位
```

> 无请求参数。AI 模块内部：
> - 从 tasks 表查活跃工单（status = new/pending/in_progress）
> - 逐条读 task_comments
> - 找最近一条 `📝 讨论摘要`（created_by = "AI任务助手"）
> - 计算上次摘要后的新人类评论数
> - ≥2 条 → 生成摘要 → 写 task_comments
> - <2 条 → 跳过

---

## 响应

```json
{
  "code": 0,
  "data": {
    "total": 15,
    "generated": 3,
    "skipped": 12,
    "failed": 0,
    "items": [
      {"task_id": "3", "summary": "...", "new_comments": 5, "skipped": false, "comment_id": 99},
      {"task_id": "7", "skipped": true, "reason": "新评论不足(1/2)", "new_comments": 1},
      ...
    ],
    "_total_ms": 2345
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `data.total` | int | 扫描的活跃工单总数 |
| `data.generated` | int | 实际生成摘要的数量 |
| `data.skipped` | int | 跳过的数量（评论不足） |
| `data.failed` | int | 失败的数量 |
| `data.items` | array | 每条工单的结果 |

---

## 触发策略（后端）

建议后端定时（如每 3 分钟）调用一次：

```
定时任务 / cron
  → POST /api/ai/task/summarize {}
  → 忽略 skipped，记录 generated/failed
```
