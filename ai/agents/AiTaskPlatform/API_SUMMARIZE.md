# 讨论摘要 API 协议

> 服务：任务 Agent（AI 服务 8401）
> 端点：`POST /api/ai/task/summarize`
> 版本：v3.0

---

## 概述

调用 AI 服务生成工单讨论区的关键进展摘要。无状态服务——不做任何 DB 操作，后端决定触发时机和数据来源。

支持两种模式：

- **首次摘要**：传入完整工单上下文 + 全部讨论记录
- **增量摘要**：只传上次摘要 + 新增讨论，上下文消耗极小（≤300字）

---

## 请求

### 首次摘要

```json
POST /api/ai/task/summarize

{
  "task_id": "44946",
  "title": "避让后车不动",
  "description": "44946避让生成的时候，车已经在这个位置了。且44946没有路径，导致车不动了。后续是人车切手动后，才重新规划并完成。",
  "diagnosis_summary": "推测: 路径规划死锁 / MAPF算法异常 / 避让场景路径生成bug | 排除: 网络通信异常 / MQTT连接断开 / 车辆硬件故障",
  "discussion_history": [
    {
      "author": "张工",
      "content": "日志拿到了，时间是14:40左右",
      "time": "2026-07-22 15:20"
    },
    {
      "author": "李工",
      "content": "应该是MAPF版本的问题，查一下是不是v1.1.2",
      "time": "2026-07-22 15:25"
    },
    {
      "author": "张工",
      "content": "确认了，v1.1.2确实有这个bug，回退到v1.1.1就好了",
      "time": "2026-07-22 15:30"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `task_id` | string | ✅ | 工单 ID |
| `title` | string | ✅ | 工单标题 |
| `description` | string | — | 工单描述 |
| `diagnosis_summary` | string | — | 提单 Agent 诊断摘要。从 `tasks.metadata_info.diagnosis` 拼接 hypotheses + ruled_out |
| `discussion_history` | array | ✅ | 讨论记录，按时间排序。每项含 `author`(string)、`content`(string)、`time`(string) |

### 增量摘要

```json
POST /api/ai/task/summarize

{
  "task_id": "44946",
  "previous_summary": "张工提供了日志（14:40左右），李工判断为MAPF版本问题（已排除网络和硬件故障），建议下一步检查MAPF版本号。",
  "discussion_history": [
    {
      "author": "王工",
      "content": "确认了，v1.1.2确实有这个问题，回退到v1.1.1就好了",
      "time": "2026-07-22 15:30"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `task_id` | string | ✅ | 工单 ID |
| `previous_summary` | string | ✅ | 上次摘要结果 |
| `discussion_history` | array | ✅ | **仅新增**的讨论记录 |

> ⚠️ **增量模式判断**：当 `previous_summary` 非空时自动走增量模板。title/description/diagnosis_summary 无需传入。

---

## 响应

```json
{
  "code": 0,
  "data": {
    "task_id": "44946",
    "summary": "张工提供了日志（14:40左右），李工判断为MAPF v1.1.2版本问题，王工确认后回退到v1.1.1解决。",
    "_trace": [ ... ],
    "_total_ms": 1234
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | 0=成功，1=失败 |
| `data.task_id` | string | 工单 ID |
| `data.summary` | string | 生成的摘要（≤150字） |
| `data._trace` | array | 埋点追踪节点（供自动化测试） |
| `data._total_ms` | int | 总耗时（毫秒） |

### 错误响应

```json
{
  "code": 1,
  "message": "LLM 服务暂时不可用"
}
```

---

## 后端实现建议

### 触发时机

```
新评论产生
  → 检查是否需要触发生成
  → 首次：累计 ≥3 条人类评论
  → 增量：上次摘要后累计 ≥2 条新评论 或 距上次摘要 ≥10 分钟
```

### 数据来源

```sql
-- diagnosis_summary 拼接
SELECT metadata_info->>'$.diagnosis.hypotheses' AS hypotheses,
       metadata_info->>'$.diagnosis.ruled_out' AS ruled_out
FROM tasks WHERE id = ?;

-- 讨论历史（首次）
SELECT * FROM task_comments
WHERE task_id = ? AND created_by != 'AI任务助手'
ORDER BY created_at ASC;

-- 增量讨论（上次摘要之后的新评论）
SELECT * FROM task_comments
WHERE task_id = ? AND created_by != 'AI任务助手'
  AND created_at > (
    SELECT MAX(created_at) FROM task_comments
    WHERE task_id = ? AND created_by = 'AI任务助手' AND content LIKE '📝 讨论摘要%'
  )
ORDER BY created_at ASC;
```

### 写入展示区

AI 返回的 `summary` 字段，由后端决定存储方式（写入 `task_comments` 或单独字段），前端在工单详情页的「AI 讨论摘要」区域渲染。
