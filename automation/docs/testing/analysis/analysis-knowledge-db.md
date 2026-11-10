# 模块分析：knowledge（知识生产闭环）· 数据库集成测试

> **任务类型**：第二阶段分析（AGENTS.md 七要素格式）
> **依据**：`backend/app/models/knowledge.py`、`backend/app/modules/admin/services/knowledge_service.py`、`knowledge_settlement.py`、`knowledge_like_quota.py`（只读）
> **状态**：待人工确认

---

## 1. 功能点（优先级）

| 功能点 | 说明 | 优先级 |
|---|---|---|
| 知识发布 | 工单关闭 → `knowledge_publications` 入库（task_id 主键，report_id 必填） | P0 |
| 审核决策 | `archive_review_decisions`，`uq_archive_review_version`（report_id+version 唯一） | P0 |
| 点赞/取消 | `knowledge_question_likes`（task_id+user_id 复合主键，软删 active）+ 事件表 `knowledge_like_events` | P0 |
| 每日点赞限流 | `DAILY_QUESTION_LIKE_LIMIT = 5`（`knowledge_like_quota.check_question_quota`） | P0 |
| 点赞风险标记 | `risk_status`(valid/其他) + `mark_like_risk`，结算冻结后禁改 | P1 |
| 月度结算 | `knowledge_settlements`（month 主键），状态机 reviewing→held→approved→granting→granted，标记风险后置 `stale` | P1 |
| 讨论 | `knowledge_discussions` 追加式评论 | P2 |
| 活动窗口 | CAMPAIGN_MONTHS = 2026-08 ~ 2026-12，窗口外 422 | P1 |

## 2. 业务流程

- **主流程（闭环）**：工单关闭 → AI 总结生成 `archive_reports` → 人工审核（approved）→ 写入 `knowledge_publications` → 其他用户点赞/讨论 → 月度结算排名
- **异常分支**：重复审核同版本（唯一约束冲突）；点赞超日限 5（422/拒绝）；结算 granting/granted 后修改风险（拒绝）；活动窗口外月份（422）
- **软删语义**：点赞取消 = `active=False` + 事件表追加一条 `active=False` 事件（审计留痕），并非物理删除

## 3. 状态流转

| 对象 | 状态 | 合法流转 | 非法流转（必须拒绝） |
|---|---|---|---|
| KnowledgeSettlement | reviewing → held → approved → granting → granted | 顺序推进 | granting/granted 后修改 rankings/risk → 拒绝 |
| KnowledgeSettlement（stale） | 任一状态 → stale | mark_like_risk 在非冻结态触发且已 granted 时自动置 stale | stale → 直接 granting（需重新 review） |
| KnowledgeLike | active=true ↔ active=false | 切换 + 事件表追加 | 同一 (task_id,user_id) 重复插入主键冲突 → 复用行 |
| KnowledgePublication | active=true → false（下架） | 软删 | 物理删除 |

## 4. 权限控制

- 结算操作 `reviewed_by` / `granted_by` 需 admin（`is_admin_user`）
- `mark_like_risk` 操作者记入 `risk_operator`，需管理角色
- 数据库层关注：权限字段是否正确落库（本层不做接口权限断言，接口权限属 P0 API 层）

## 5. 接口/数据列表

| 表 | 主键/约束 | 测试断言焦点 |
|---|---|---|
| `knowledge_publications` | PK=task_id；NOT NULL×9 | 非空约束、LONGTEXT 变体 |
| `archive_review_decisions` | `uq_archive_review_version`(report_id,version) | **唯一约束冲突 → IntegrityError** |
| `knowledge_question_likes` | 复合 PK(task_id,user_id) | 复合主键去重、软删切换 |
| `knowledge_like_events` | autoincrement id | 事件追加顺序、created_at 索引 |
| `knowledge_settlements` | PK=month(String(7))；rankings/audit 为 JSON | JSON 字段读写往返、状态字段 |
| `knowledge_discussions` | autoincrement id | 追加式写入 |

## 6. 风险点

| 风险 | 等级 | 缓解 |
|---|---|---|
| 审核版本唯一约束被绕过（并发双写） | 高 | 集成测试直接触发 IntegrityError 验证 DB 层兜底 |
| 结算快照 stale 后仍可发放奖励 | 高 | 断言 granting 前置状态校验（业务层）+ 落库状态正确 |
| 软删点赞在排名中仍计数 | 中 | `like_state` 过滤 `active AND risk_status='valid'` 的落库断言 |
| JSON 字段（rankings/audit）跨驱动序列化差异 | 中 | 写入后读回深度断言 |
| LONGTEXT 内容截断 | 中 | 写入超长内容读回比对 |

## 7. 边界条件

| 场景 | 预期行为 |
|---|---|
| 同一 report_id 提交 version 1、1、2 | 第二条 IntegrityError，第三条成功 |
| 复合主键 (task,user) 二次点赞 | 复用行切 active，不新增行（likes 表）；events 表新增 |
| 月度字段 "2026-13" / "202608" | 业务层 422；DB 层 String(7) 截断风险验证 |
| rankings 写入 → 读回 | JSON 深度相等 |
| 超长 content（>64KB） | LONGTEXT 完整存取 |
| 活动窗口外月份查询排名 | 422（业务层），DB 无写入 |

---

*分析完成。等待人工确认后进入设计阶段。*
