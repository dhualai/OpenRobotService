# 设计：剩余业务场景用例（②⑤⑧⑨）

> **状态：设计稿 —— 待排期，尚未实施。**
> 本文仅记录方案与已核实的接口契约，不代表已排期或已批准。
> 实施前请先确认范围（尤其 ⑥⑩⑦ 依赖 AI 服务与 MinIO，当前不具备条件）。

确认时间：未确认
编写日期：2026-10-26
前置：已完成 ①③④⑦（见 `task-local-loop-verification.md` 与 `tests/real/test_ticket_scenarios.py`）

## 一、范围

10 条业务场景中剩余 6 条，本设计覆盖其中 **4 条不依赖外部服务的**：

| 编号 | 场景 | 是否纳入本期 | 原因 |
|---|---|---|---|
| ② | 协商阶段推进 | ✅ 纳入 | 纯接口，本地已有 3 个阶段模板 |
| ⑤ | 转派 / 再派单 | ✅ 纳入 | 纯接口，已有两个账号 |
| ⑧ | 关联工单与前后置依赖 | ✅ 纳入 | 纯接口 |
| ⑨ | 被代理人确认 | ✅ 纳入 | 需直接写库造关系（表已存在） |
| ⑥ | AI 自动派单 + 理由可见性 | ❌ 本期不做 | 依赖 AI 服务 8401，未部署 |
| ⑩ | 我要摇人 AI 问答 → 提单 | ❌ 本期不做 | 同样依赖 AI 服务；Mock 已有 1 条全链路 |
| ⑦ | 附件上传（⑦ 的剩余部分） | ❌ 本期不做 | 依赖 MinIO（9000），未部署 |

## 二、接口契约（已从源码核实）

### ② 协商阶段

| 接口 | 入参 | 说明 |
|---|---|---|
| `POST /api/tasks/{id}/complete-step` | `{next_step_id: int, curr_step_endtime: "ISO"}` | 当前阶段完成，推进下一节点 |
| `POST /api/tasks/{id}/negotiate-step` | `{curr_step_endtime: "ISO", curr_step_id?: int, reason: str}` | **reason 必填**，记录为评论；节点仅可当前或之后 |
| `POST /api/tasks/{id}/set-step-time` | `{curr_step_endtime: "ISO"}` | **仅 `escalate_count > 0` 的工单**可用，本期不测 |

约束（`task.py:2933`）：目标节点必须属于该 `task_type`，正常 step 要求 `sequence > 当前`。

### ⑤ 转派 / 再派单

| 接口 | 入参 | 说明 |
|---|---|---|
| `PATCH /api/tasks/{id}/assign?user_id=<users.id>` | **query 参数**，非 body | 任何已登录用户可改派 |
| `POST /api/tasks/{id}/re-dispatch` | `{preferred_assignee: str, remark?: str}` | 清空 `assigned_to`、状态回 `new`、写 `metadata_info.preferred_assignee`，并向 Redis 发布重新派单事件 |
| `POST /api/tasks/reassign-stats` | — | 统计历史转派，只读 |

### ⑧ 关联工单

| 接口 | 入参 | 说明 |
|---|---|---|
| `POST /api/tasks/{id}/relations` | `{target_task_id: int, relation_type: "predecessor"\|"duplicate"\|"subtask"}` | 需 `backend:tasks:operate` 权限 |
| `GET /api/tasks/{id}/relations` | — | 双向查询 |
| `GET /api/tasks/{id}/relations/tree` | — | 关系树 |

约束（`task.py:2955`）：推进阶段前必须已有一条 `predecessor` 关系；`predecessor` 未完成会阻塞当前工单完成/关闭；`duplicate` 仅标记不阻塞。

### ⑨ 被代理人确认

| 接口 | 入参 | 说明 |
|---|---|---|
| `GET /api/tasks/{id}/proxy-relations` | — | 查关系 |
| `POST /api/tasks/{id}/proxy-relations/{relation_id}/ack` | — | 被代理人确认跟进，获得协办权 |
| `POST /api/tasks/{id}/proxy-relations/{relation_id}/decline` | — | 与我无关（终态，工单不中断） |

**关键限制**：仓库内**没有创建代理关系的 API**，`backend/scripts/create_task_proxy_relation.py` 只建表不插数据。
因此用例需要直接向 `task_proxy_relation` 表插入一行（`task_id / agent_id / principal_id / relation_status='pending'`）。

## 三、文件与职责

| 文件 | 类型 | 职责 |
|---|---|---|
| `automation/tests/real/_ticket_flow.py` | 新增 | 真实链路公共辅助：登录、取 user id、建单、派单、读阶段、状态流转、撤回收尾 |
| `automation/tests/real/test_ticket_scenarios.py` | 修改 | 追加场景 ②（协商阶段）2 条 |
| `automation/tests/real/test_ticket_collaboration.py` | 新增 | 场景 ⑤⑧⑨ 共 5 条（协作类） |
| `automation/docs/task-local-loop-verification.md` | 修改 | 追加本期结果 |

不复用现有 `test_ticket_lifecycle_real.py` / `test_ticket_scenarios.py` 内的局部 helper（避免改动已通过用例）；
新增的 `_ticket_flow.py` 先只服务新文件，后续再统一迁移。

## 四、用例清单（7 条）

### 场景 ②（加入 test_ticket_scenarios.py）

| # | 测试函数 | 覆盖类型 | 断言要点 |
|---|---|---|---|
| 1 | `test_negotiate_step_requires_reason` | 数据校验 | 缺 `reason` → 400「协商理由必填」；带 reason + endtime → 200，返回 `curr_step_endtime` |
| 2 | `test_complete_step_rejects_cross_type_node` | 状态流转 | 传不属于该 `task_type` 的 step_id → 400 |

### 场景 ⑤（新文件）

| # | 测试函数 | 覆盖类型 | 断言要点 |
|---|---|---|---|
| 3 | `test_assign_changes_owner` | 正常流程 | `PATCH assign?user_id=` → 200，`GET` 回读 `assigned_to` 已变 |
| 4 | `test_redispatch_resets_to_new` | 状态流转 | 已派单的单 `re-dispatch` → `status=new`、`assigned_to` 为空 |

### 场景 ⑧（新文件）

| # | 测试函数 | 覆盖类型 | 断言要点 |
|---|---|---|---|
| 5 | `test_predecessor_relation_created` | 正常流程 | 建 B → A 关联 B（predecessor）→ 200；`GET relations` 能查到 |
| 6 | `test_relation_requires_operate_permission` | 权限 | `plain_user` 建关联 → 403 |

### 场景 ⑨（新文件）

| # | 测试函数 | 覆盖类型 | 断言要点 |
|---|---|---|---|
| 7 | `test_principal_ack_and_decline` | 状态流转 + 权限 | 造代理关系 → principal `ack` → 200（获协办权）；另一单 `decline` → 200 且关系终态 |

## 五、Mock 变更

**无。** 本期全部走真实后端：
Mock 未实现 `reopen-step`、`proxy-relations`、`relations`、`negotiate-step`。
若后续要进 CI 门禁，需先补 Mock（另立任务）。

## 六、实现步骤

1. 起环境：MySQL 3306 + 后端 8400，`GET /api/health` 200
2. 新增 `_ticket_flow.py`（只抽新文件要用的 6 个辅助函数）
3. 写场景 ② 两条 → 单跑通过
4. 写场景 ⑤ 两条 → 单跑通过
5. 写场景 ⑧ 两条 → 单跑通过
6. 写场景 ⑨ 一条（含直接写 `task_proxy_relation`）→ 单跑通过
7. 全量回归：`USE_MOCK=0 pytest tests/real -q` → 17 passed（原 10 + 新 7）
8. Mock 回归：`pytest tests -q` → 仍 131 passed（新用例在 tests/real 下默认 skip）
9. 更新验证文档 → 提交

## 七、风险

| 风险 | 影响 | 处置 |
|---|---|---|
| **Redis 未启动** | `re-dispatch` 发布事件失败 | 只断言 DB 状态（`status=new`、`assigned_to` 空），不断言派单 Worker 后续动作 |
| **代理关系无创建 API** | 用例需直连库写数据 | 仅本地/有库环境可跑；CI 里需另建辅助步骤，本期不纳入门禁 |
| `set-step-time` 需 `escalate_count>0` | 无法直接构造 | 本期不测，留待升级流程专项 |
| 关联权限 `backend:tasks:operate` | 只有 admin/automation_bot 有 | 负例用 `plain_user`，正例用 admin |
| 工单不可删除（P1 缺陷） | 每轮累积脏数据 | 收尾统一 `PATCH canceled`；标题前缀 `AUTO-` 便于日后清理 |
| 真实链路较慢 | 7 条约 3~5 分钟 | 单独目录，不进 fast lane |
| 前置关系阻塞规则未验证 | 可能 400/409 与预期不符 | 步骤 5 先只验证「能建、能查」；阻塞语义实测后决定是否加断言 |

## 八、验收标准

- `USE_MOCK=0 pytest tests/real -q` → 全部通过，输出真实耗时
- Mock 全量不受影响（131 passed）
- `automation/docs/task-local-loop-verification.md` 更新本期结论
- 提交到独立分支并附 PR
