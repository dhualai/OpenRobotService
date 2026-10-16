# 摇人吧（OpenRobotService）数据库关系模型（ERD）

> 生成方式：以代码中的 **ORM 真实定义**为准（唯一定义点 `backend/app/models/`），并与本地库 `helpdesk_7_16` 实测的 37 张表、14 条物理外键逐一核对。
> 生成日期：2026-09-14 ｜ 库：MySQL 8（字符集 utf8mb4） ｜ 时间字段统一 **naive UTC**

> 增量说明（2026-10-15）：下文37表为2026-09-14实测基线，并非当前ORM总表数。本次贡献榜及讨论新增6张表及归档报告价值点，详见2.7；本机dev已迁移至 `d0e3f6a9b215`，生产及其他环境未执行，不能把ORM定义视为所有环境的已部署结构。

## 0. 数据来源与口径

| 项 | 说明 |
|---|---|
| ORM 唯一定义点 | `backend/app/models/`（`base.py` 单一 `declarative_base()`，Alembic 导入本包即注册全部模型） |
| 表总数 | **37 张**（与本地库 `information_schema` 实测完全一致） |
| 其他镜像模型 | `ai/core/database.py`、`co/db.py` 只是**只读投影**（users / tasks / project / risk / conversations / messages / user_project_roles），不额外建表 |
| 独立存储 | MinIO（对象存储，对应 `resources` 元数据）、Redis（会话/缓存）、Meilisearch（检索索引）、`app/kb/*.sqlite`（知识库），均非本 ERD 范围 |
| 关系口径 | 物理外键（DB 真实约束）与**逻辑外键**（代码里存 ID、无 DB 约束）分开标注——本项目大量关联是逻辑外键，这是读图时最关键的注意点 |

---

## 1. 总览：37 张表按域划分

| 域 | 表 |
|---|---|
| 身份 / RBAC（5） | `users`、`roles`、`permissions`、`role_permissions`、`user_project_roles` |
| 组织主数据（2） | `companies`、`departments` |
| 任务 / 工单（9） | `tasks`、`task_comments`、`task_comment_read`、`task_comment_read_record`、`task_operation_logs`、`task_steps`、`task_spec_doc`、`task_dispatch_log`、`task_user_mapping` |
| AI 历史工单（1） | `tickets`（AI 诊断生成，已逐步被 `tasks` 取代） |
| 对话 / 消息（4） | `conversations`、`messages`、`dataqa_conversations`、`dataqa_messages` |
| 资源（2） | `resources`、`resource_folders` |
| 交付项目 / 数据（9） | `project`、`risk`、`project_daily_report`、`project_license`、`project_transport_efficiency`、`project_transport_efficiency_robot`、`realtime_data`、`history_data`、`collection_data` |
| 责任模块树（3） | `module_trees`、`module_tree_nodes`、`module_tree_edits` |
| 统计 / 杂项（2） | `user_info`、`user_statistics` |

---

## 2. 分域 ER 图

### 2.1 身份 / RBAC / 组织 / 项目

```mermaid
erDiagram
    companies ||--o{ departments : "company_id"
    companies ||--o{ users : "company_id(逻辑)"
    departments ||--o{ users : "department_id(逻辑)"
    users ||--o{ users : "supervisor_id 上级(模型FK,库内或未落)"
    users ||--o{ user_project_roles : "user_id"
    roles ||--o{ user_project_roles : "role_id"
    project ||--o{ user_project_roles : "project_id"
    roles ||--o{ role_permissions : "role_id"
    permissions ||--o{ role_permissions : "permission_id"
    users ||--o{ resources : "owner_id(逻辑)"
    project ||--o{ tasks : "project_id(逻辑)"

    users {
        varchar id PK
        varchar username UK
        varchar password_hash
        varchar name
        varchar status
        text external_credentials "USP 凭证 JSON"
        int avatar_resource_id "逻辑→resources.id"
        varchar company "废弃,改用company_id"
        varchar department "废弃,改用department_id"
        varchar company_id FK
        varchar department_id FK
        json responsibility_modules "三层 产品/界面/功能"
        tinyint job_level "1一线 2管理 3兜底"
        text duty_text "AI 派单画像"
        varchar supervisor_id FK
        varchar wechat_openid "微信转发接收"
        varchar phone "企微@人"
    }
    roles {
        varchar id PK
        varchar name UK
        varchar role_type "system/project"
    }
    permissions {
        varchar id PK
        varchar code UK
        varchar name
        text description
        varchar resource_type
        varchar action
        varchar enabled
    }
    role_permissions {
        varchar id PK
        varchar role_id FK
        varchar permission_id FK
    }
    user_project_roles {
        varchar id PK
        varchar user_id FK
        varchar project_id FK "可空=全局角色"
        varchar role_id FK
        varchar report_to_id FK "项目内汇报对象"
    }
    companies {
        varchar id PK
        varchar name UK
        varchar status "pending/approved/rejected"
        varchar created_by "存ID无FK"
        datetime created_at
        varchar approved_by
        datetime approved_at
        varchar reject_reason
    }
    departments {
        varchar id PK
        varchar name "UQ(name,company_id)"
        varchar company_id FK
        varchar status
        text profile_text "部门职责画像"
        json examples "典型工单示例"
        varchar created_by
        datetime created_at
    }
    project {
        varchar id PK "与 code 一致"
        varchar code UK
        varchar name
        varchar system_id "MQTT/外部标识"
        varchar contact_person_id "逻辑→users.id"
        varchar project_manager_id "逻辑→users.id"
        varchar status "已删除=软删"
        varchar undertake_status "是/待定"
        varchar settlement_period "业绩核算期"
        json stage_notes
        json project_documents
    }
```

### 2.2 任务 / 工单域（核心）

```mermaid
erDiagram
    tasks ||--o{ task_comments : "task_id"
    tasks ||--o{ task_operation_logs : "task_id"
    tasks ||--o{ task_dispatch_log : "task_id"
    tasks ||--|| task_spec_doc : "task_id(UQ 一工单一文档)"
    tasks ||--o{ task_comment_read : "task_id(逻辑,无物理FK)"
    tasks ||--o{ task_comment_read_record : "task_id"
    task_comments ||--o{ task_comments : "reply_to 引用(逻辑)"
    task_comments ||--o{ task_comment_read_record : "comment_id(逻辑)"
    tasks }o--o{ task_steps : "curr_step_id(逻辑)"
    users ||--o{ tasks : "created_by/assigned_to(逻辑)"

    tasks {
        bigint id PK
        varchar title
        text description
        varchar task_type "problem/bug/feature/support/other"
        varchar status "new/in_progress/pending/resolved/canceled/closed"
        varchar priority "low/medium/high/urgent"
        varchar created_by "username"
        varchar assigned_to "users.id"
        varchar project_id "逻辑→project.id"
        varchar project_name "冗余"
        bigint related_resource_id "逻辑→resources.id"
        datetime created_at
        datetime updated_at
        datetime resolved_at
        datetime deadline_at
        json tags
        json metadata_info "AI 整体覆盖写"
        json attachments
        json attachment_analysis "AI 附件分析记忆"
        varchar source "manual/zentao"
        varchar external_id "UQ(source,external_id)"
        varchar external_url
        bigint curr_step_id "逻辑→task_steps.id"
        varchar curr_step_name
        int step_negotiation_round
        int step_phase_round
        boolean curr_step_agreed
        int escalate_count
    }
    task_comments {
        bigint id PK
        bigint task_id FK
        text content
        varchar created_by
        boolean is_public
        json attachments
        bigint reply_to "逻辑自引用"
        datetime created_at
        datetime updated_at
    }
    task_comment_read {
        bigint id PK
        bigint task_id "无物理FK"
        varchar username
        bigint last_read_comment_id "游标"
        datetime updated_at
    }
    task_comment_read_record {
        bigint id PK
        bigint task_id FK
        bigint comment_id "逻辑→task_comments.id"
        varchar username
        datetime read_at
    }
    task_operation_logs {
        bigint id PK
        bigint task_id FK
        varchar operation_type "create/assign/status_change/view..."
        varchar operator
        varchar operator_name
        varchar to_status
        json detail
        datetime created_at
        int duration_seconds "仅 VIEW"
    }
    task_dispatch_log {
        bigint id PK
        bigint task_id FK
        int dispatch_round "第N轮派单"
        varchar preferred_id "意向人"
        varchar assigned_id "实际接单人"
        float confidence
        varchar decision_type "auto/recommend/fallback"
        text reasoning
        json profile "被派人画像+missing"
        json candidates "精排Top10快照"
        boolean name_collision
        boolean pinyin_match
        datetime created_at
    }
    task_steps {
        bigint id PK
        varchar task_type
        varchar step_name
        int sequence
    }
    task_spec_doc {
        bigint id PK
        bigint task_id FK "UQ 一工单一文档"
        longtext content "markdown"
        varchar source "inline/upload/ai_summary"
        json source_files
        int revision "乐观锁"
        datetime updated_at
    }
    task_user_mapping {
        bigint id PK
        varchar source "zentao/..."
        varchar external_account "UQ(source,account)"
        varchar external_realname
        varchar local_user_id
    }
    tickets {
        int id PK
        varchar session_id "AI 会话"
        varchar ticket_ai_id
        varchar status "pending_dispatch/..."
        varchar created_by
        json diagnosis "Agent 诊断链"
        json attachments
        datetime created_at
    }
```

### 2.3 对话 / 消息域（摇人 + 数据助手，两套隔离）

```mermaid
erDiagram
    conversations ||--o{ messages : "conversation_id"
    messages ||--o{ messages : "parent_message_id(逻辑)"
    dataqa_conversations ||--o{ dataqa_messages : "conversation_id"
    dataqa_messages ||--o{ dataqa_messages : "parent_message_id(逻辑)"

    conversations {
        int id PK
        varchar title
        varchar user_id
        varchar scene_type "chat/faq/support/consultation/other"
        varchar service_ticket_id "关联工单"
        text metadata_
        datetime created_at
        datetime updated_at
    }
    messages {
        int id PK
        int conversation_id FK
        varchar role "user/assistant/system"
        varchar message_type "text/image/file/audio/multimodal"
        text content
        text file_urls
        int parent_message_id "逻辑自引用"
        int sequence
        datetime created_at
        text metadata_
    }
    dataqa_conversations {
        int id PK
        varchar title
        varchar user_id
        text metadata_
        datetime created_at
    }
    dataqa_messages {
        int id PK
        int conversation_id FK
        varchar role
        text content
        int parent_message_id
        int sequence
        datetime created_at
    }
```

### 2.4 资源域（MinIO 元数据）

```mermaid
erDiagram
    resource_folders ||--o{ resource_folders : "parent_id(逻辑)"
    resource_folders ||--o{ resources : "folder_id(逻辑)"
    users ||--o{ resource_folders : "无 owner 列(逻辑)"

    resources {
        bigint id PK
        varchar resource_name
        varchar resource_hash_code UK "秒传去重"
        varchar owner_id "逻辑→users.id"
        varchar resource_type "file/image/video/..."
        varchar resource_status
        varchar resource_url
        bigint resource_size
        varchar storage_type "MINIO/OSS"
        bigint folder_id "逻辑→resource_folders.id"
        json extra_metadata
        datetime created_at
        datetime deleted_at "软删"
    }
    resource_folders {
        bigint id PK
        varchar folder_name
        bigint parent_id "逻辑自引用"
        varchar path
        int level
        json child_folder_ids
        json child_resource_ids
        datetime deleted_at
    }
```

### 2.5 交付项目 / 数据域

```mermaid
erDiagram
    project ||--o{ risk : "project_code(逻辑)"
    project ||--o{ project_daily_report : "project_code(逻辑)"
    project ||--o{ project_license : "project_code(逻辑)"
    project ||--o{ project_transport_efficiency : "project_code(逻辑)"
    project ||--o{ project_transport_efficiency_robot : "project_code(逻辑)"

    risk {
        int id PK
        varchar risk_code UK
        varchar project_code "逻辑→project.code"
        varchar project_name "冗余"
        varchar risk_category
        text description
        varchar risk_level
        varchar responsible_person_id
        varchar status
        varchar discovery_time
    }
    project_daily_report {
        int id PK
        varchar project_code
        varchar report_date "UQ(project_code,report_date)"
        text report_content "JSON"
        varchar reporter_id
    }
    project_license {
        int id PK
        varchar project_code
        varchar machine_code
        varchar apply_time
        varchar expire_time
        text license_code
        varchar applicant_id
        int max_vehicles
    }
    project_transport_efficiency {
        int id PK
        varchar project_code
        varchar report_date "UQ(project_code,report_date)"
        int total_tasks
        float effective_work_hours
        float fault_hours
        float manual_intervention_rate
    }
    project_transport_efficiency_robot {
        int id PK
        varchar project_code
        varchar report_date
        varchar robot_model "UQ(项目+日期+型号)"
        int carry_task_total
        float effective_efficiency
    }
    realtime_data {
        int id PK
        varchar project "项目名字符串"
        varchar indicator
        text data
        varchar collection_time
        varchar record_time
    }
    history_data {
        int id PK
        varchar project
        varchar indicator
        text data
        bigint start_time
        bigint end_time
        varchar time_str
    }
    collection_data {
        int id PK
        varchar project
        varchar indicator
        bigint start_time_int
        bigint end_time_int
        text data
    }
```

### 2.6 责任模块树（AI 派单主数据）+ 统计表

```mermaid
erDiagram
    module_trees ||--o{ module_tree_nodes : "product(逻辑,同名产品)"
    module_tree_nodes ||--o{ module_tree_edits : "product+func(逻辑)"
    users ||--o{ module_tree_edits : "requester_id(逻辑)"

    module_trees {
        varchar product PK "产品名"
        json tree_json "整树:产品→界面→功能"
        datetime updated_at
    }
    module_tree_nodes {
        int id PK
        varchar product
        varchar iface_name
        int iface_order
        varchar func_name "UQ(product,func_name)"
        int func_order
        json keywords
        text anchor
        json engineers "负责工程师 user_id 数组"
        datetime updated_at
    }
    module_tree_edits {
        int id PK
        varchar product
        varchar iface_key
        varchar func_key
        json old_json
        json new_json
        varchar requester_id
        json owner_ids "需同意的原负责人"
        varchar status "pending/approved/rejected/cancelled"
        varchar decider_id
        datetime decided_at
    }
    user_info {
        int id PK
        json user_info
        date created_time
    }
    user_statistics {
        int id PK
        date ref_date
        int user_source
        int new_user
        int cancel_user
    }
```

---

### 2.7 一期知识贡献榜与匿名审核（2026-10-15增量）

ORM：`backend/app/models/knowledge.py`。Alembic：`b8c1d4e7f203 → c9d2e5f8a104 → d0e3f6a9b215`，后一版本仅增加公开知识讨论表。以下ID关系均为逻辑关联，无新增物理外键；时间按naive UTC保存，活动按北京时间自然月计算。

| 表 / 字段 | 主键与关键口径 |
|---|---|
| `knowledge_publications` | `task_id`主键，一工单一条；关联当前通过报告 `report_id`，固定首次提单/解答人和 `first_approved_at`，保存公开正文与匿名意见快照；正文MySQL LONGTEXT |
| `knowledge_question_likes` | `(task_id, user_id)`联合主键；保存激活状态、风险状态、当前原因、标记人及更新时间 |
| `knowledge_like_events` | 自增 `id`，关联工单与用户；记录每次点赞/取消状态及时间，用于还原月末状态 |
| `archive_review_decisions` | 自增 `id`，`(report_id, version)`唯一；保存审核人、通过/驳回意见、价值点和当时报告，正文MySQL LONGTEXT；仅意见作者可查询本人历史 |
| `knowledge_settlements` | `month`主键；一期固定11月和12月，保存修订版本、综合排名JSON、核验/发放状态及审计JSON |
| `knowledge_discussions` | 自增INTEGER `id`；BIGINT `task_id`、VARCHAR(64) `author_id`、TEXT `content`、DATETIME `created_at`；工单、作者和时间各有索引。正文1—2000字，登录用户仅可读写仍有效公开的问题讨论；不复用 `task_comments`，不计贡献或奖励 |
| `archive_reports.value_point` | 新增可空TEXT，通过审核时必填；审核意见继续使用现有 `review_comment` |

```mermaid
erDiagram
    tasks ||--o| knowledge_publications : "一工单一条发布"
    archive_reports ||--o{ knowledge_publications : "当前公开报告"
    archive_reports ||--o{ archive_review_decisions : "按提交版本留存"
    users ||--o{ archive_review_decisions : "非公开审核人"
    knowledge_publications ||--o{ knowledge_question_likes : "点赞明细"
    knowledge_publications ||--o{ knowledge_like_events : "点赞时间线"
    users ||--o{ knowledge_question_likes : "每人每题一条"
    tasks ||--o{ knowledge_discussions : "公开知识讨论(逻辑)"
    users ||--o{ knowledge_discussions : "讨论作者(逻辑)"
```

`knowledge_settlements.rankings`固化名次、贡献/获赞数及预计奖金；入账复用 `reward_record`，来源类型 `knowledge_monthly`，来源ID按一期/月份/用户生成幂等键，不新增另一套余额或支付流水。

## 3. 关系总表（父子 + 基数 + 是否物理约束）

### 3.1 物理外键

模型声明共 16 处 `ForeignKey`；本地库 `information_schema` 实测落库 **14 条**（`users.company_id`、`users.department_id`、`users.supervisor_id` 中仅部分环境存在，见下方备注）：

| 父表 | 子表 | 关联字段 | 基数 | 删除行为 | 落库 |
|---|---|---|---|---|---|
| `users` | `user_project_roles` | user_id | 1:N | — | ✅ |
| `users` | `user_project_roles` | report_to_id | 1:N | — | ✅ |
| `roles` | `user_project_roles` | role_id | 1:N | — | ✅ |
| `project` | `user_project_roles` | project_id | 1:N | — | ✅ |
| `roles` | `role_permissions` | role_id | M:N | — | ✅ |
| `permissions` | `role_permissions` | permission_id | M:N | — | ✅ |
| `companies` | `departments` | company_id | 1:N | — | ✅ |
| `tasks` | `task_comments` | task_id | 1:N | CASCADE | ✅ |
| `tasks` | `task_comment_read_record` | task_id | 1:N | CASCADE | ✅ |
| `tasks` | `task_operation_logs` | task_id | 1:N | CASCADE | ✅ |
| `tasks` | `task_dispatch_log` | task_id | 1:N | CASCADE | ✅ |
| `tasks` | `task_spec_doc` | task_id | 1:1 | CASCADE | ✅ |
| `conversations` | `messages` | conversation_id | 1:N | CASCADE | ✅ |
| `dataqa_conversations` | `dataqa_messages` | conversation_id | 1:N | CASCADE | ✅ |
| `users` | `users` | supervisor_id | 1:N（自引用） | — | ⚠️ 模型声明，本地库未落 |
| `companies` | `users` | company_id | 1:N | — | ⚠️ 模型声明，本地库未落 |
| `departments` | `users` | department_id | 1:N | — | ⚠️ 模型声明，本地库未落 |

> 注：`users.company_id` / `users.department_id` 在**模型**里声明了 FK，但本地库实测未落物理约束（历史上手工加列），按逻辑外键处理更安全。

### 3.2 逻辑外键（代码存 ID、无 DB 约束，共 20 处——最易踩坑）

| 子表.字段 | 指向 | 说明 |
|---|---|---|
| `users.avatar_resource_id` | `resources.id` | 头像资源 |
| `users.responsibility_modules`（JSON） | 模块树 | 结构 `{产品:{界面:[功能]}}` |
| `companies.created_by / approved_by` | `users.id` | 刻意不建 FK，避免与 users 循环依赖 |
| `departments.created_by / approved_by` | `users.id` | 同上 |
| `tasks.created_by / assigned_to` | `users.id` | 存 username / id |
| `tasks.project_id` | `project.id` | 同时冗余 `project_name` |
| `tasks.related_resource_id` | `resources.id` | |
| `tasks.curr_step_id` | `task_steps.id` | 冗余 `curr_step_name / curr_step_endtime` |
| `task_comment_read.task_id` | `tasks.id` | **无物理 FK**，需应用层清理 |
| `task_comment_read_record.comment_id` | `task_comments.id` | 唯一键 (comment_id, username) |
| `task_comments.reply_to` | `task_comments.id` | 自引用（消息引用/回复） |
| `task_dispatch_log.preferred_id / assigned_id` | `users.id` | |
| `task_user_mapping.local_user_id` | `users.id` | |
| `task_spec_doc.created_by / updated_by` | `users.id` | |
| `conversations.service_ticket_id` | 任务 ID（字符串） | |
| `messages.parent_message_id` | `messages.id` | 自引用 |
| `resources.owner_id` / `resources.folder_id` | `users.id` / `resource_folders.id` | |
| `resource_folders.parent_id` | `resource_folders.id` | 自引用 |
| `risk / project_daily_report / project_license / *_transport_efficiency*.project_code` | `project.code` | 交付域普遍用 **code 字符串**关联，非 id |
| `module_tree_nodes.product`、`module_tree_edits.product` | `module_trees.product` | 产品名关联 |
| `module_tree_nodes.engineers`（JSON 数组） | `users.id` | 负责工程师 |

---

## 4. 设计要点速查（读图必看）

1. **任务是统一抽象**：`tickets`（AI 诊断旧表）与 `tasks`（现行工单表）并存，新代码一律用 `tasks`；`Task.ticket_type` 是 `task_type` 的兼容 property。
2. **两套「已读」互补**：`task_comment_read`= 每用户每工单游标（`last_read_comment_id`）；`task_comment_read_record`= 每条评论 × 每人的已读名单（飞书式）。二者不可互相替代。
3. **一工单一问题文档**：`task_spec_doc` 独立成表 + `revision` 乐观锁。原因：AI `upsert_task` 会整体覆盖 `tasks.metadata_info`，文档若塞进 metadata 会被反复覆盖。
4. **派单可审计**：`task_dispatch_log` append-only，一轮派单一行（含 Top10 候选快照、置信度、同名/拼音命中标记），前端只取 `dispatch_round` 最大的一条。
5. **交付域用项目 code 而非 id 关联**：`project.code` = `project.id`，风险/日报/License/运输效率表全部以 `project_code` 关联，且大量字段是字符串时间（`String(20/30)`），非 DATETIME。
6. **软删除三处**：`project.status='已删除'`（保留编号/名称占位）、`resources.deleted_at`、`resource_folders.deleted_at`。
7. **冗余字段是刻意的**：`tasks.project_name/curr_step_name`、`risk.project_name`、`project_transport_efficiency_robot` 的明细列——为免联表直接展示，改主数据时需同步。
8. **唯一约束清单**：`uq_task_source_external`(tasks)、`uq_task_read_user`、`uq_comment_read_user`、`uq_mapping_src_account`、`uq_task_spec_doc_task`、`uq_department_name_company`、`uq_module_tree_nodes_product_funcname`、`project.code`、`risk.risk_code`、`users.username`、`roles.name`、`permissions.code`、`resources.resource_hash_code`，以及交付域的 (project_code, report_date[+robot_model]) 组合唯一。

---

## 5. 关于 CodeQL

CodeQL 是**语义代码查询 / 漏洞审计**引擎（数据流 Source→Sink 分析），它的强项是找注入、越权等安全缺陷，**不适合**做 ER 模型抽取——它没有"ORM 实体—关系"的领域抽象，且建库（CodeQL database）对 Python 全仓需数十分钟，产出仍只是 AST/调用图，还得再写大量手写 QL 才能还原字段与关系，精度不如直接读 `declarative` 定义。

本项目数据库结构的**唯一可信来源**就是 `backend/app/models/`（`Base = declarative_base()` 单一 metadata），配合 `information_schema` 校验，结果如上。若确实需要"代码变更自动同步 ERD"，更合适的做法是：写一个脚本 import `app.models` → 遍历 `Base.metadata.tables` → 生成 Mermaid（比 CodeQL 短两个数量级且零误差）。需要的话我可以补这个生成器。
