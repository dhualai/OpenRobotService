# 项目扩展信息（ext_info）与项目信息树（info_nodes）后端接口变更说明

> 变更日期：2026-09-14（初版）；2026-09-15 新增 5.8 文件识别接口；2026-09-16 新增 4.4 AI 项目摘要接口、5.9 / 5.10 节点操作记录（编辑历史）接口、5.11～5.13 节点关注与项目动态接口；2026-09-17 4.4 的大模型客户端改为 backend 自维护的 `app/core/llm_client.py`（不再依赖仓库根 `ai/core/llm.py`，`requirements.txt` 移除 tenacity）；同日性能修复：info-nodes 组纯同步路由去 async（改走 FastAPI 线程池，避免同步 DB 阻塞事件循环）、parse-file 文件抽取移入线程池（`asyncio.to_thread`）、5.13 项目动态查询改 `GROUP BY max(id)` 按主键回查（不再全量拉历史）、info_node 三 service 与 project_service 收敛共享 `app/core/db.py` 引擎（`pool_pre_ping`/`pool_recycle`，空闲连接失效自愈）；2026-09-20 新增 5.16 企业微信台账同步预览接口（项目信息编辑页「同步」：把台账列名与信息节点对齐后给出 填写 / 覆盖 / 新增 三组预览，**不落库**）；2026-09-21 5.16 的台账数据源改为**本地 `project` 表镜像**（不再请求 AI 服务的 /api/ai/wecom/projects，删掉 503 失败态与「台账里找不到记录」的 400，路由由 async 改回同步 def、走线程池）；同日 5.9 新增 `include_descendants` 子树口径：一级标签的「历史」汇总这一级下所有节点的变动（含子树里被删节点的删除记录），前端按节点分组渲染成 Markdown 文档（第九节第 9、17 条）；2026-09-21 5.16 的匹配新增**按值认节点**（台账列的值正好是某个下拉节点的可选项时认到那个节点上，列名对不上也认，`_pin_option_values`），且「同步」不再新建一级标签——没归属的列只作提醒，不再堆进「导入信息」兜底根节点（第九节第 16 条）；2026-09-21 基础模板调整（迁移 `6f2c8a1d9b47`，第三节）：`基础信息` 下新增 项目编号/订单号/时间信息汇总（+5 子节点）、`硬件/车型信息`（原「车辆」改名）下新增 总车数、`调度软件/版本` 下新增 版本号；台账列 `项目编号` 随之不再是定位列（要填进同名节点）、文件导入提示词同步更新（车型归属路径按「车型N 槽位的父级」定位 + 新增「总车数」规则）；2026-09-21 新增 5.17 一键清空接口（`POST /projects/{id}/reset-to-template`：把本项目的信息树**恢复成模板的样子**——删掉导入/同步/增补加进来的节点（`project_id` 非空，含子孙）+ 清掉全部已填值；全局字段定义、下拉选项、编辑历史保留，增补节点上的关注随节点清掉，附件只解除挂载；逐条记 `delete` 历史并加一条整树级记录，门槛同结构类写接口；编辑页按钮在「同步」右侧。首版只清值不动结构，同日按用户口径改为「清理成模板的结构」）
> 模块：`app/modules/admin`（后台管理）
> 路由公共前缀：`/api/admin`（`/api` 来自 `API_V1_STR`，`/admin` 来自 `admin_router`）

## 一、功能概述

本次为支撑「项目信息页」改造，后端做了五项变更：

| # | 变更 | 存储位置 | 目的 |
|---|------|----------|------|
| 1 | `project` 表新增 `ext_info` JSON 列 | 主表 | 承接递归嵌套、变化频繁的展示型字段（overview / activity） |
| 2 | `project` 表新增 `version` 乐观锁列 | 主表 | 防止整文档读改写模式下多人并发编辑互相覆盖 |
| 3 | 新增 `project_info_node` 表及 6 个接口 | 独立子表 | 用户可自由增删/拖拽/编辑的信息大纲树，逐节点 CRUD 互不干扰 |
| 4 | 新增 `project_info_node_change` 表及 2 个查询接口（5.9 / 5.10） | 独立子表 | 每个节点操作留痕（时间/人员/节点/具体变动），编辑页「历史」可查、有未读新变动出小红点 |
| 5 | 新增 `project_info_node_mark` 表及 3 个接口（5.11～5.13） | 独立子表 | 展示页「项目信息管理」子节点可点星标关注（**按人隔离，每人一份**）；被关注节点的**最新**变动聚合进「项目动态」卡 |

**职责边界（重要）**

- `ext_info` 存「开发者定义结构的值」——结构固定、面向展示；创建时由 YAML 模板初始化。
- `project_info_node` 存「结构本身就是用户数据」——节点可由用户自由增删、拖拽排序、编辑值，每个节点独立写入，不会因整树读改写而丢更新。
- `info_nodes` 数据**不放在** `ext_info` 中，模板里的 `info_nodes` 段在项目创建时被拆分写入子表。

## 二、数据库变更

### 2.1 迁移脚本

| 迁移版本 | 日期 | 内容 |
|----------|------|------|
| `a7b8c9d0e1f2` | 2026-09-09 | `project` 表增加 `ext_info`、`version` 两列 |
| `b2c3d4e5f6a7` | 2026-09-14 | 新建 `project_info_node` 表（依赖前一迁移） |

### 2.2 project 表新增列

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `ext_info` | JSON | NULL | 项目扩展信息，递归嵌套字典/数组；仅存 `overview` + `activity` |
| `version` | INT | NOT NULL DEFAULT 1 | 乐观锁版本号，每次更新成功自增 1 |

ORM 定义：[delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py#L147-L153)（`Project` 类，JSON 列由 ORM 自动反序列化为 dict）。

### 2.3 project_info_node 新表

邻接表（adjacency list）模型：

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | VARCHAR(64) | PK | 节点 UUID（创建节点时由客户端生成；模板实例化时服务端生成） |
| `project_id` | VARCHAR(64) | NOT NULL | 所属项目 ID（= project.id / project_code） |
| `parent_id` | VARCHAR(64) | NULL | 父节点 ID，NULL 表示根节点 |
| `title` | VARCHAR(255) | NOT NULL | 节点标题 |
| `content_type` | VARCHAR(32) | NOT NULL DEFAULT 'text' | 内容类型：text/image/file/... |
| `value` | TEXT | NULL | 节点值 |
| `sort_order` | INT | NOT NULL DEFAULT 0 | 同级排序 |
| `created_at` / `updated_at` | VARCHAR(30) | NOT NULL | 字符串时间戳，格式 `YYYY-MM-DD HH:MM:SS` |

索引：

- `idx_pn_project (project_id)` —— 按项目查树
- `idx_pn_parent (parent_id)` —— 按父节点查子级
- `idx_pn_project_parent_sort (project_id, parent_id, sort_order)` —— 同级有序查询

ORM 定义：[delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py#L294-L322)（`ProjectInfoNode` 类，经 `models_das/models.py` 再导出）。

### 2.4 project_info_node_change 新表（节点操作记录）

每次节点操作写一行；与业务**同一个事务**提交（不另开连接），避免「节点改了但没记」或反之。表由 `Base.metadata.create_all` 在启动时自动创建，无需迁移脚本。

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | VARCHAR(64) | PK | 记录 id，**时间有序的 UUIDv7**（`created_at` 只到秒，同秒多条的先后靠它兜底排序） |
| `project_id` | VARCHAR(64) | NOT NULL | 所属项目 ID |
| `node_id` | VARCHAR(64) | NULL | 被操作节点 ID；整树级操作（import / 模板重建 / 详情模板同步）为 NULL |
| `parent_id` | VARCHAR(64) | NULL | 上级节点 ID —— **删除记录据此挂到父节点历史里**；根节点被删为 NULL |
| `node_title` | VARCHAR(255) | NOT NULL DEFAULT '' | 操作当时的节点标题快照（改名/删除后仍能看清当时是谁） |
| `action` | VARCHAR(16) | NOT NULL | `create` / `update` / `move` / `delete` / `import` / `sync` |
| `operator` / `operator_name` | VARCHAR(64) | NULL | 操作人用户名 / 显示名；识别不到时为 NULL（前端显示「未知用户」） |
| `detail` | TEXT | NULL | 具体变动的人话描述（服务端拼好，前端直接展示） |
| `created_at` | VARCHAR(30) | NOT NULL | 字符串时间戳 `YYYY-MM-DD HH:MM:SS` |

索引：`idx_pnc_project_time (project_id, created_at)`、`idx_pnc_node (project_id, node_id)`、`idx_pnc_parent (project_id, parent_id)`。

ORM 定义：[delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py)（`ProjectInfoNodeChange` 类）；读写集中在 [info_node_change_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_change_service.py)。

**记录的触发点与粒度**

| 操作 | 记录条数 | action | node_id / parent_id | detail 示例 |
|------|----------|--------|---------------------|-------------|
| 新建节点（5.2） | 1 | `create` | 新节点 / 其父 | 新建节点「充电区位置」 |
| 更新节点（5.3） | 有一条实质变动才记（逐字段对比 title/content_type/value/sort_order，无变化不记） | `update` | 节点 / —— | 把标题从「基础」改为「基础信息」；把内容从「空」改为「中力」 |
| 移动/排序（5.4） | 换父或同级序号确实变了才记 | `move` | 节点 / —— | 把「叉车1」从「车辆」移到「设备」下 |
| 删除节点（5.5） | 1（整棵子树只记一条，detail 含子树节点数） | `delete` | **被删节点 / 其父** | 删除节点「车辆」及其 3 个子节点 |
| 批量导入（5.6） | 1（整树级） | `import` | NULL / NULL | 导入信息树：写入 120 个节点（原有 121 个节点被替换） |
| 按模板重建（5.7） | 1（整树级） | `import` | NULL / NULL | 按预设模板重建信息树：写入 120 个节点（原有 0 个节点被替换） |
| 详情模板同步 | 每个受影响项目 1 条（整树级） | `sync` | NULL / NULL | 详情模板同步：新增 2 个、更新 5 个、删除 1 个节点 |

**展示归属**：节点 X 的编辑历史 = `node_id = X` 的记录 + 「`parent_id = X` 且 `action = delete`」的记录。删除记录挂在**上级节点**上——节点删掉后自身查询入口没了，用户要求「删除节点在其上级节点显示删除记录」。整树级记录（node_id 为 NULL）入库但不进任何单节点历史，只在项目级查询里可见。

**人员识别**：写接口用 `get_request_actor_optional`（`api/auth.py`）尽力解析 `Authorization: Bearer <token>`（`decode_token` → `get_user_with_roles`，取 `username` / `name`）。**不强制鉴权**：解析失败/无 token 一律按匿名记录（operator 为 NULL），保持本组路由「网关管控」的现状不被破坏。

### 2.5 project_info_node_mark 新表（节点关注标注）

展示页「项目信息管理」卡每个子节点（一级标签之下的节点）右侧有星标，点击即写入/删除一行——**关注列表就是这张表**，「项目动态」卡按它聚合。表由 `Base.metadata.create_all` 在启动时自动创建，无需迁移脚本。

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `node_id` | VARCHAR(64) | **PK（联合）** | 被关注的节点 ID |
| `operator` | VARCHAR(64) | **PK（联合）** | 关注人登录名（JWT sub）—— 同一节点可被多人各存一行 |
| `project_id` | VARCHAR(64) | NOT NULL | 所属项目 ID（按项目查列表用） |
| `operator_name` | VARCHAR(64) | NULL | 关注人显示名（识别不到时为登录名；产品上不展示，仅追溯用） |
| `created_at` | VARCHAR(30) | NOT NULL | 字符串时间戳 `YYYY-MM-DD HH:MM:SS` |

索引：`idx_pnm_project_user (project_id, operator)`。

ORM 定义：[delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py)（`ProjectInfoNodeMark` 类）；读写与聚合集中在 [info_node_mark_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_mark_service.py)。

**按人隔离（用户明确要求）**：「自己关注的自己才能看到，每个人可能关注的节点不一样」。星标状态（5.11）、切换（5.12）、项目动态（5.13）都按当前登录人过滤，别人的关注互不可见、互不影响；服务端以主键里的 `operator` 区分，不存「共享关注」。因此这三个接口**必须能识别出登录人**（Bearer token 的 `sub`），取不到返回 401（见 5.11 备注）。

> 建表沿革：本表 2026-09-16 上午的初版是「整项目共享」（单主键 `node_id` + `created_by` 列）；同日调整为用户口径的「每人一份」（联合主键 `node_id + operator`）。存量库若已有旧结构空表，直接 `DROP TABLE project_info_node_mark` 后重启即可（`create_all` 会按新模型重建）；旧表里有数据时先 `UPDATE ... SET operator = COALESCE(created_by, '')` 之类的口径确认再迁移。

**级联清理**：节点（含子树）被删除（5.5）、整树被导入替换（5.6 / 5.7 import-template）、详情模板同步（`sync`）删除节点时，标注随节点在同一事务里删除（`remove_marks` / `clear_project_marks`，按节点全量删、不区分人），避免留下点不开的孤儿关注。`project_info_node_change` 里的历史记录**不清理**（历史要可追溯）。

## 三、YAML 模板初始化机制

- 模板目录：[project_templates/](file:///d:/CODE/9_9/OpenRobotService/backend/app/config/project_templates)，当前提供 `default.yaml`（13 个根节点、共 131 个节点，对齐《项目信息树形图》）。
- 选择规则：按项目的 `project_type` 找 `{type}.yaml`，文件不存在则回退 `default.yaml`；新增模板只需加文件，无需改代码。
- **全局节点怎么进库**（新结构下 `default.yaml` 只用来播种一次，之后以库里的全局节点行为准）：历史库由迁移 `7c1e9a4b2d38` 播一次；**空库**（测试环境/新机器，不跑本机迁移历史）由后端启动时的 `info_node_seed_service.ensure_global_info_nodes()` 自动补齐——一个全局节点都没有才整棵写入，已有节点的库原样跳过，不改不删。两处播出的节点 id 都是「标题路径 → 确定性 UUIDv5」，跨环境一致。
- **此后改模板 = 改库，光改 `default.yaml` 对已有库没有任何影响**（`ensure_global_info_nodes` 只在「一个全局节点都没有」时播）。存量库要同步模板改动，必须再写一条 alembic 迁移把它做进库——最近的例子是 `6f2c8a1d9b47`（见下），更早的是 `9d2f4a6b8c01` / `4a7c2e9d1b53`（车型改下拉）。迁移里的 id 要按**标题路径**推（与 `_seed_node_id` 同源），否则新库播种与老库迁移会得到两套 id。
- **2026-09-21 模板改动（迁移 `6f2c8a1d9b47`，用户要求）**：
  - `基础信息` 下新增 `项目编号`、`订单号`、`时间信息汇总`（子节点：`项目创建时间` / `业绩核算期` / `初次接触时间` / `预计AGV下线时间` / `预计进场时间`），三条排在最前，原有的客户信息/订单信息/评审信息/项目区域·地点/项目类型/进厂要求整体后移（同级序号重排，相对顺序不变）；
  - `硬件 / 车辆` **改名** `硬件 / 车型信息`——同一个节点，`node_key`（`hardware.vehicle`）与 id 都不动（改名只改 `node_name`，id 仍按老路径推，见 `info_node_seed_service._ID_PATH_ALIASES`）；其下新增 `总车数`，并排在 `车型1`/`车型2` 之前；
  - `调度软件 / 版本` 下新增 `版本号`，排在既有两项之后。
  - 迁移对存量库同样幂等（按 `node_key` 查在不在，在就一行不动），只改数字与名字，不动任何项目的值；`downgrade` 删新节点（连同各项目已填的值）、名字改回「车辆」、序号重排回连续值。
  - 连带影响：「同步信息」（5.16）的台账列 `项目编号` **不再是定位列**，树里有同名节点就直接填进去；文件导入的提示词同步更新（车型分组改名后按「车型N 槽位的父级」定位、新增「总车数」规则，见 5.8）。
- 加载与缓存：模板在 Service 层集中加载，进程内按类型缓存，每次返回**深拷贝**避免调用方污染缓存。
- **容错**：模板解析失败（缩进/编码错误）只记 error 日志并按空模板处理，不让项目接口整体 500（`ext_info` 为 NULL 的存量项目读取时也会走模板）。
- 模板分三段：
  - `overview` → `ext_info.overview`（progress / tags / wecom_id / ai_summary 等标量）
  - `activity` → `ext_info.activity`（version_changes / stage_changes 事件数组）
  - `info_nodes` → 递归树**定义**，节点字段：

| 字段 | 说明 |
|------|------|
| `title` | 节点标题（必填） |
| `sort_order` | 同级排序，缺省按书写顺序 |
| `content_type` | `text`（默认）/ `select` / `file` / `image`；有 `options` 时缺省即 `select` |
| `options` | 仅 `select` 用；实例化时写入 `value = {"selected":"","options":[...]}`（与前端下拉解码一致） |
| `value` | 预置值（可选）：`text` 用字符串，其余按 `content_type` 的结构 |
| `children` | 子节点（递归） |

- 模板结构有两条硬约束（前端 UI 的既定行为）：
  1. **最深 4 层**（`PROJECT_INFO_MAX_DEPTH = 4`，第 4 层不可再挂子节点）；
  2. **一级标签只作分组**：根节点的值类型一律归为 `text`（`normalize_template_nodes` 归一），模板页也不给它渲染内容类型选择器。
  - 「`select` 必须是末级」这条旧约束已在 2026-09-18 取消：一个节点可以既带值又有子节点（车型1 = 下拉选型号 + 其下「数量」子节点）。可填值的判据统一为 `有子节点 ? content_type !== 'text' : true`，前后端同一口径。
- **增补的唯一边界是层级（2026-09-18 起）**：`allow_custom` 闸门已取消——「详情模板」页不再有「允许各项目在此节点下增补信息」勾选框，`add_custom_node` 也不读这个字段，任何节点下都能增补（新建节点与模板保存一律写 `allow_custom = true`，存量行由迁移 `5b8e3f2a9c47` 补齐）。唯一的边界是层级——**最多 4 层**，第 5 层起 `400「信息层级最多 4 层，该位置不能再往下加」`（`MAX_INFO_DEPTH`）。
  - 思维导图里第 5 层的「可选值清单」仍表达为 `content_type: select` + `options`，不必展开成子节点。
- **区域联动**（仅前端渲染行为，接口与数据不变）：`基础信息 → 项目区域/地点` 下，`区域选项` 这个下拉含 `大陆(China Mainland)` 选项。前端据此联动——选「大陆」时显示 `省份`/`地区`，选其它非空区域时显示 `具体国家`，未选择时三者都不显示；节点始终在数据里（切回时原值还在），只是隐藏渲染。`省份`/`地区`/`具体国家` 三个标题不能改名，否则联动失效（实现见前端 `projectInfoTree.ts` 的 `isInfoNodeVisible`）。旧版模板初始化过的项目若缺 `具体国家` 节点，需补一个同级节点才能生效。

实现：[project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py) 中的 `_get_ext_info_template()` / `_split_template()` / `get_info_nodes_template()` / `template_node_value()`。

## 四、项目接口的修改

路由文件：[projects.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/projects.py)
Service：[project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py)
请求/响应模型：[request_models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/schemas_das/request_models.py)

### 4.1 响应体新增字段（影响所有项目查询接口）

以下接口返回的每个项目对象均新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ext_info` | object \| null | 扩展信息；**库中为空（迁移前的老项目）时，读取时自动用模板的 overview+activity 填充返回，但不写回数据库**，直到下次保存才持久化 |
| `version` | int | 乐观锁版本号，老数据为 NULL 时按 1 返回 |

涉及接口（路径与行为不变，仅响应体扩充）：

- `GET /api/admin/projects/` 项目列表
- `GET /api/admin/projects/me` 当前用户关联项目
- `GET /api/admin/projects/{project_id}` 项目详情

Service 逻辑（`_convert_to_dict`）：序列化主表列后附加两字段；`ext_info` 为空时调用 `_split_template(project_type)` 取模板部分（只取 overview+activity，info_nodes 走独立表不混入）。

### 4.2 POST /api/admin/projects/ —— 创建项目（行为增强）

请求体 `ProjectCreate` 新增可选字段 `ext_info`。

Service 逻辑（`create_project`）：

1. 常规字段处理：project_code → code/id 映射、JSON 字段（field_links / stage_notes / project_documents / system_integration）序列化、白名单过滤表列。
2. 按 `project_type` 拆分模板：
   - 请求未传 `ext_info` 时，用模板的 overview+activity 初始化；显式传入则以传入值为准。
   - `info_nodes` 树**总是**从模板生成，不受请求体影响。
3. 插入 project 行并提交。
4. 递归实例化信息树：为模板每个节点生成**全新 UUID**（不同项目同模板不撞主键），value 留空，保持模板的 title / sort_order / 层级，批量写入 `project_info_node`，再次提交。

冲突响应（沿用唯一性校验）：项目编号或名称已存在返回 `409`，detail 为「项目编号「xx」已存在，请重新输入」等；并发提交撞唯一约束同样兜底为 409。

### 4.3 PUT /api/admin/projects/{project_id} —— 更新项目（新增乐观锁）

请求体 `ProjectUpdate` 新增：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ext_info` | object | 否 | 扩展信息整体提交（整文档读改写） |
| `version` | int | 否 | 编辑前从详情接口读到的版本号；**前端编辑提交必须带回** |

Service 逻辑（`update_project`）：

1. 从更新数据中取出 `version`（不参与字段赋值）。
2. `SELECT ... FOR UPDATE` 行锁锁定该项目行（过滤软删除），串行化同项目并发更新；项目不存在返回 None → API 层 404。
3. **版本校验**：请求带了 version 且与库中当前值（NULL 视为 1）不一致 → 回滚释放行锁，抛 `ProjectConflictError`，API 层转为 `409`，detail 形如「项目已被他人修改（当前版本 3，提交版本 2），请刷新后重试」。
4. 请求**不带** version 时跳过校验，保持旧行为（供内部系统更新使用，如企业微信同步）。
5. 应用字段更新（JSON 字段空值置 NULL、白名单过滤），随后 `version = 当前值 + 1`，提交并返回最新项目（含新 version）。

前端处理约定：捕获 409 后提示用户刷新页面、基于最新数据重新编辑，不得静默重试覆盖他人修改。

### 4.4 POST /api/admin/projects/{project_id}/ai-summary —— AI 项目摘要（新增）

- 用途：后台管理-项目详情页「项目概况」卡底部「AI 项目摘要」卡片；点「AI 生成 / 重新生成」时，后端读取项目基础字段 +「项目信息管理」整棵信息树，由大模型总结项目基础情况，写回 `ext_info.overview.ai_summary` 并随响应返回。
- 请求：无 body。
- Service 逻辑（`project_ai_summary_service.generate_for_project`）：
  1. 读该项目的完整信息树（同 5.1），为空直接 400（提示先初始化信息树）；
  2. 组装提示词（纯函数 `build_summary_prompt`）：项目 25 个已入库基础字段按中文标签逐行输出（空值跳过）+ 信息树按「父路径 / 子节点：内容」逐行渲染——text 折叠空白、select 解 `{"selected":...}`（非 JSON 旧数据按原文兜底）、file/image 取文件名；**空值节点不输出正文**，只统计为「（另有 N 个末级节点未填写）」附在末尾；正文超 12,000 字符截断；
  3. 调大模型：接口用 backend 自维护的 `app/core/llm_client.py` 的 `LLMClient`（httpx 异步、读超时 60s、网络异常自动重试至多 3 次，不依赖仓库根 `ai/core/llm.py` 与 tenacity），密钥/模型取 backend 配置（`settings.LLM_API_KEY` / `LLM_API_URL` / `LLM_MODEL_NAME`，即**与文件识别（5.8）同一个 DeepSeek flash**）；temperature=0.3、max_tokens=1500、非流式、显式关闭思考链；
  4. 清洗输出（去 ``` 围栏/首尾引号）后深拷贝 `ext_info` 合并 `overview.ai_summary`，走 `project_service.update_project` 落库（内部写入**不带 version**、跳过乐观锁，version 仍 +1，与企微同步同约定）；保存失败（项目被删）抛 400。
- 响应 `200`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `summary` | string | 生成的摘要正文（结构化 Markdown：`## 小节` + `- 要点`，250 字以内） |
| `model` | string | 实际使用的模型名 |
| `ext_info` | object | 写库后的完整 ext_info，前端直接替换本地状态即可，无需重新拉详情 |

- 错误：`400`（信息树暂无节点 / 保存时项目已被删除）；`404`（项目不存在）；`503`（`LLM_API_KEY` 未配置或大模型调用失败——密钥无效、限流、超时重试耗尽等，detail 带中文原因）。
- 提示词由两部分固定文案 + 动态资料组成：system「只输出总结本身（无代码块围栏/解释/寒暄）」；user「【项目基础字段】+【项目信息管理】+【输出格式】（结构化 Markdown：固定小节顺序——项目概况/硬件与车型/系统与部署/交付与进度/风险与关注点，无资料的小节省略；每节 1～2 条要点、全文 250 字以内、加粗最多 3～4 处；**不得编造**、不重复、不用套话）」。前端用 react-markdown 渲染该 Markdown。

### 4.5 Schema 变更汇总

- `ProjectBase`：新增 `ext_info: Optional[Dict[str, Any]]`
- `ProjectUpdate`：新增 `ext_info`、`version: Optional[int]`
- `ProjectResponse`：新增 `ext_info`、`version: int = 1`

## 五、项目信息树接口（新增，18 个）

路由文件：[info_nodes.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/info_nodes.py)
Service：[info_node_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_service.py)、[info_node_import_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_import_service.py)（仅 5.8）
路由前缀：`/api/admin/info-nodes`，tag：`admin-info-nodes`

> 鉴权口径（2026-09-20 起，见 `api/info_nodes.py` 顶部注释与 `tests/test_info_nodes_authz.py`）：
>
> | 接口 | 谁能调 |
> |------|--------|
> | 结构类：5.2 创建节点、5.6 批量导入、5.8 AI 识别、5.17 一键清空（恢复为模板结构）、5.3 更新节点、5.4 移动、5.5 删除 | **该项目下的人**（`user_project_roles` 里该项目有任一角色）或 admin — `require_project_member` |
> | 5.x 节点级路由（`/nodes/{node_id}`）| 同上，项目不在路径上，按节点反查归属项目（`_require_node_project_member`）|
> | 5.1 树查询、5.9/5.10 历史、5.11～5.13 关注与动态 | 沿用网关管控，不额外鉴权（普通用户本来就要看项目信息）|
> | `PUT /nodes/{id}/value` 值写入 | 任何登录用户（只能写已存在节点的值，不改结构）|
> | `POST /projects/{id}/custom-nodes` 增补信息 | 任何登录用户（限父节点必填、层数 ≤ 4）|
> | `GET/POST /template` 详情模板 | 模板权限码 `frontend:admin:project-info-template:show` 或 admin — 由**全局角色**「开发者 / 超级管理员」派生（`permission_service._GLOBAL_ROLE_DERIVED_PERMISSIONS`）|
>
> **全局字段定义**（`project_id` 为空的行）不属于任何项目，改它等于改全体项目：节点级闸门对它放行，由 Service 层回 `403 全局字段定义请在「详情模板」里修改`——真正的原因归 Service 说，闸门不拿「越权」搪塞。
> 写接口额外挂了 `get_request_actor_optional`（**尽力识别、不拦截**）：带 `Authorization: Bearer` 时把操作人记进操作记录（2.4），不带/解析失败按匿名记录。

节点对象标准字段：`id, project_id, parent_id, title, content_type, value, sort_order, created_at, updated_at`；树查询时每节点额外含 `children` 数组。

### 5.1 GET /info-nodes/projects/{project_id} —— 获取信息树

- 功能：返回项目完整信息树的递归嵌套结构。
- Service 逻辑：一次查出该项目全部节点并按 `sort_order` 排序；在 Python 内构建 `parent_id → 子节点` 映射，从 `parent_id IS NULL` 递归组装 children；空树返回 `[]`。项目节点量级为百级，不使用递归 CTE 以兼容 MySQL 版本。
- 响应：`200`，节点数组（根节点列表），每节点含 `children`。

### 5.2 POST /info-nodes/projects/{project_id} —— 创建节点（增补，仅本项目可见）

- 鉴权：**该项目下的人**或 admin（`require_project_member`）；不是这个项目的人 → `403 只有该项目下的人员可以编辑项目信息树`。
- 状态码：`201`
- 请求体 `InfoNodeCreate`：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `parent_id` | string \| null | 否 | null | 父节点 ID，null 为最外层（编辑页的「新标签」走这里）|
| `title` / `node_name` | string | 是（二者取一）| —— | 节点名称 |
| `content_type` | string | 否 | "text" | 内容类型：text/select/file/image |
| `value_type` | string | 否 | —— | 值类型，优先于 `content_type` |
| `node_key` | string | 否 | 自动生成 | 节点外部标识 |
| `sort_order` | int | 否 | 末尾 | 同级排序 |

- Service 逻辑：在**本项目范围内**增补一个自定义节点（`project_id` 落成该项目，不动全局模板、别的项目看不到），逐级插入后返回节点。可加在任意层级（这条路径不限 4 层；限层的是下方的 `/custom-nodes`）。

### 5.3 PUT /info-nodes/nodes/{node_id} —— 更新节点

- 鉴权：按节点反查归属项目，**该项目下的人**或 admin（`_require_node_project_member`）；全局字段（`project_id` 为空）由闸门放行、Service 回 `403 全局字段定义请在「详情模板」里修改`；别的项目的增补节点 → `403 只有该项目下的人员可以编辑项目信息树`。
- 请求体 `InfoNodeUpdate`：`title` / `content_type` / `sort_order` / `required` / `allow_custom` / `options` / `titleOptions` 均可选；**不能改 parent_id**（换父请用 move 接口），`options`/`titleOptions` 只对本项目增补的节点生效。
- 业务规则：请求体剔除值为 None 的字段后若为空 → `400 {detail: "无更新字段"}`。
- Service 逻辑：按 id 查节点，不存在返回 None → `404 {detail: "节点不存在"}`；仅对白名单四字段逐个赋值，刷新 `updated_at`，提交返回更新后节点。

### 5.4 PATCH /info-nodes/nodes/{node_id}/move —— 移动/排序节点

- 鉴权：同 5.3（按节点归属项目判是否本项目的人）。
- 请求体 `InfoNodeMove`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `new_parent_id` | string \| null | 是 | 目标父节点，null 表示移到根 |
| `new_sort_order` | int | 是 | 目标同级位置，默认 0 |

- 功能：拖拽排序/换父节点。仅更新 parent_id、sort_order、updated_at；节点不存在 → 404。
- 已知约束：服务端**不做环检测**（拖入自身子树的防护由前端树形控件保证）。

### 5.5 DELETE /info-nodes/nodes/{node_id} —— 删除节点（含整棵子树）

- 鉴权：同 5.3；全局字段不在此删除（它属于模板，停用请在 `/template` 保存时移除）。
- Service 逻辑：先用**递归 CTE**（`WITH RECURSIVE`，MySQL 8）查出该节点及全部后代 ID；结果为空（节点不存在）→ 404；否则按 ID 集合批量删除并提交。
- 响应：`200 {"detail": "已删除节点及其子树"}`。

### 5.6 POST /info-nodes/projects/{project_id}/import —— 批量导入信息树

- 请求体 `InfoNodeImport`：`{"nodes": [ {id, title, content_type?, value?, sort_order?, children?: [...]} ]}`，递归嵌套。
- Service 逻辑：**纯增补**——递归展平入参为行列表（parent_id 在展平过程中按层级挂上，缺省值同创建）后批量插入；同 `node_key` 的节点已存在就跳过，不动全局定义、不动已有值。
- 响应：`200 {"imported": <新增节点数>}`。
- 适用场景：从 a.json 等外部信息树整体迁入。
- 历史：旧实现是「先清空该项目全部旧节点再导入」；新结构下项目不再持有节点副本，那等于抹掉项目已填的全部信息，故改为只增不改不删。

### 5.7 POST /info-nodes/projects/{project_id}/import-template —— 按项目模板重建信息树

- 请求体：无（项目 id 走路径参数）。
- 功能：读该项目 `project_type` 对应的 YAML 模板（缺省 `default.yaml`），实例化整棵信息树并**替换**该项目现有全部节点（与新建项目的初始化同一份模板定义）。
- Service 逻辑：查项目（软删除项目视为不存在 → `404`）→ `get_info_nodes_template(project_type)` 取模板 → 递归生成 UUID 与 `content_type` / `value`（`options` 编码为 `{"selected":"","options":[...]}`）→ 复用 `import_tree`（先清空后批量插入）。
- 响应：`200 {"imported": <节点总数>}`；模板为空或解析失败时**不改动现有节点**，返回 `{"imported": 0}`。
- 适用场景：功能上线前创建、信息树为空的存量项目一键初始化；前端信息编辑页打开时**自动**触发（空树才跑、按项目只跑一次，避免「替换式导入」重复建树），空态按钮保留为自动初始化失败/模板为空时的手动重试入口。

### 5.8 POST /info-nodes/projects/{project_id}/parse-file —— AI 识别导入文件（预览，**不落库**）

- 鉴权：与落库同门槛——**该项目下的人**或 admin。识别要读整棵树、又要花全平台的模型配额，且会把项目信息回显给调用方，只读身份不构成放开的理由。
- 请求：`multipart/form-data`，字段 `file`（单个文件，≤10MB）。
- 支持格式与抽取方式（全部在后端完成，前端不引解析库）：

| 扩展名 | 抽取方式 |
|--------|----------|
| `.docx` | 标准库 `zipfile` 读 `word/document.xml`，段落逐行、表格行转 `单元格 \| 单元格` |
| `.xlsx` | `openpyxl`（read_only），每个工作表以 `# 工作表：名字` 分隔、制表符分列 |
| `.md` / `.markdown` / `.txt` / `.csv` | 按 `utf-8-sig → gbk → utf-8(replace)` 解码 |
| `.doc` / `.xls` | 明确拒绝，提示另存为 `.docx` / `.xlsx` |

- Service 逻辑（`info_node_import_service.analyze_import_file`）：
  1. 校验大小/格式并抽取正文；正文超过 100,000 字符截断（响应 `truncated=true`）；
  2. 读该项目信息树，展平为「节点目录」（每行 `路径<TAB>类型[<TAB>(末级)][<TAB>可选项：a|b]`）——只把目录与文件正文放进 prompt，**不要求大模型输出整树**；
  3. 调大模型（与「摇人」共用 `settings.LLM_API_KEY` / `LLM_API_URL` / `LLM_MODEL_NAME`，即 DeepSeek flash；temperature=0.2、超时 120s、非流式），要求只输出 JSON `{"items":[{"title","value","nodeTitle","quantity","suggestedParentPath"}]}`；prompt 要求「把握 ≥ 0.9 才填 nodeTitle，否则给 suggestedParentPath」「select 节点 value 必须命中可选项，否则按未匹配」「车型条目一款一条、数量写在 `quantity`（不要另起「数量」条目），旧型号 XS1161 一律写作 XS1201」「整车总台数（文件写明的总数）单出一条给 `总车数`，不许把各车型数量加起来当总数」（信息树里有「总车数」节点时才加这条规则，见 `find_vehicle_total_count_path`）；
  4. 解析返回（容忍 ```json 围栏与前后杂文字）后由**后端做权威匹配**（大模型的 nodeTitle 仅作提示）：
     - 节点标题/完整路径去空白标点后精确匹配优先，`difflib.SequenceMatcher` 相似度 **≥ 0.9（满分 1）** 兜底模糊匹配，**0.75～0.9 这一档只认「包含关系」**（`TITLE_FALLBACK_THRESHOLD`，2026-09-21 新增：两个标题差的是几个字的增删——「ERP模块」→「ERP」、「公网IP地址」→「公网ip」——才认；换了字的近似不认，「是否承接」vs「是否对接」相似度恰好 0.75，认了会把台账 156 个项目的承接与否填进「数字孪生 / 是否对接」；**只有标题这一层这样放松，选项那一层仍是 0.9**——值写进哪个下拉比标题认哪个节点要严，见下一条）；同名节点按 `suggestedParentPath` 消歧，路径也消歧不了时优先取「能装下这个值的下拉」（项目下常残留旧导入造的 text 版「车型1」，不能写进那个孤儿节点）；
     - select 节点的识别值 → 可选项，从严到宽三层：精确（忽略大小写/空白标点）→ **车型目录内的型号放宽一层**（大小写/连字符差异、旧型号名 XS1161→XS1201、值里夹带中文全称或数量、括号后缀；一个值里认出多个不同型号时返回 None，宁可让用户手动归属）→ 整串相似度 ≥ 0.9；都对不上就降级为未匹配（「试点项目一期」相似度只有 0.8，不会被吸到「试点项目」上）。命中时写进预览的是**选项原文**，不是文件的写法；
     - **下拉兜底**（2026-09-20 新增）：条目还没落到节点上时（没匹配到 / 匹配到的下拉装不下这个值 / 该节点已被前面的条目占用），先拿识别内容去比「建议归属附近」那些**空着**的下拉的可选项，命中就在下拉里选它，而不是到 `unmatched` 里新建一个与下拉各说各话的节点。「附近」= 建议归属节点本身 + 其下 2 层（`NEARBY_DROPDOWN_DEPTH`）+ 归属节点自己是下拉时的兄弟节点（车型1 被前面的条目占用后轮到车型2）。命中的选项还要与下拉「对得上」：要么选项本身是车型型号（车型目录是封闭集合，值自证身份，不靠标题），要么条目标题与该下拉标题相似度 ≥ 0.9——「是/否」「动态密码/静态密码」这类短选项不靠标题兜底的话，任何一条值写着「是」的信息都会钻进附近随便一个是否型下拉。附近下拉已选着别的值时不抢（那个值多半是别的条目填的）；选着同一个值视为已满足（不产生变更，也不建重复节点）。车型条目经此落到某个「车型N」后，其 `quantity` 照旧进该车型的「数量」子节点；
     - 同节点多条去重：**同一个值**重复出现直接丢弃（不去占第二个同类型下拉），值不同才另寻空位；识别值与节点现值一致则跳过；
     - 车型条目的 `quantity` 直接落进该车型节点下的「数量」子节点（不靠大模型另给一条「数量」条目——同名子节点在各车型下都有）；该车型下还没有「数量」子节点时，把数量推成一条 `unmatched`（`suggestedParentPath` = 车型节点的路径），由前端创建成「数量」子节点；
  5. 分桶返回三类：节点现值为空 → `fill`；非空且与识别值不同 → `overwrite`；无匹配节点 → `unmatched`（`suggestedParentPath` 逐段解析为 `suggested_parent_id`，层级上提到 ≤4 层，解析不到则 null，由前端用「导入信息」根兜底）。
- 响应 `200`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `file_name` / `model` | string | 文件名 / 实际使用的模型名 |
| `text_length` / `truncated` / `extracted` | int / bool / int | 抽取字符数 / 是否截断 / 大模型识别条目数 |
| `fill` / `overwrite` | 数组 | 元素：`node_id, path, title, content_type, current, value`（`current` 为空串=将填写） |
| `unmatched` | 数组 | 元素：`title, value, quantity, suggested_parent_id, suggested_parent_path`（`quantity` 为车型条目自带数量、该车型下没有「数量」子节点时的兜底提示，通常为 null） |

- 错误：`400`（格式不支持/内容为空/项目无数节点）；`503`（`LLM_API_KEY` 未配置或大模型调用失败，detail 带中文原因）。
- **本接口不写库**：前端预览勾选后，用 5.3 更新（fill/overwrite）与 5.2 创建（unmatched）逐节点落库；未匹配且无归属的条目挂到按需创建的「导入信息」根节点下。

### 5.9 GET /info-nodes/projects/{project_id}/changes —— 获取节点操作记录（编辑历史）

- 查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `node_id` | string | 否 | 无 | 只看某节点的历史（自身记录 + 直接子节点的删除记录）；不传则返回该项目全部记录（含整树级） |
| `include_descendants` | bool | 否 | false | 把范围放大到该节点的**整棵子树**（要先给 `node_id` 才有意义）。前端只在一级标签上这么请求：根节点的「历史」看的是这一级下所有节点的变动，见第九节第 17 条 |
| `limit` | int | 否 | 100 | 1～500，超出校验失败返回 422（子树口径前端传 200） |

- Service 逻辑（`info_node_change_service.list_for_node` / `list_for_subtree` / `list_project_changes`）：按 `project_id`（+ 命中规则）过滤，`ORDER BY created_at DESC, id DESC`（同秒内的先后用时间有序的 UUIDv7 兜底），`limit` 截断。
- 子树口径（`list_for_subtree`）取两条并集，与单节点口径同规则、只是把「直接子节点」放大成「子树里的所有节点」：`node_id` 落在子树里的记录 + `parent_id` 落在子树里且 `action = delete` 的记录。后一条不能省：删除是整棵子树一条记录挂在被删节点的上级上，被删的子孙已不在节点表里，光看 `node_id` 会把它们的删除记录整片漏掉。节点范围按读树的同一把尺子取（启用中的全局节点 ∪ 本项目增补节点）。`node_id` 指向的节点已被删除时，子树退化成它自己（与单节点口径一致）。
- 响应 `200`：

```json
{ "changes": [ { "id": "...", "project_id": "P1", "node_id": "n1", "parent_id": null,
  "node_title": "基础信息", "action": "update", "operator": "admin", "operator_name": "张三",
  "detail": "把内容从「空」改为「中力」", "created_at": "2026-09-16 14:42:13" } ] }
```

- 说明：空历史返回 `{"changes": []}`（不区分「节点不存在」与「无记录」——节点可能已被删除，历史仍需可查，见 5.5 的删除记录）。

### 5.10 GET /info-nodes/projects/{project_id}/changes/summary —— 各节点最新记录 id（小红点）

- 请求：无参数。
- 用途：前端进入编辑页、以及每次保存成功后各拉一次，与本机「已读水位」（localStorage，按「项目 + 登录用户」存）比较，算出哪些节点的历史按钮要出小红点（未读的节点 + 它所在的一级节点，见第九节第 11 条）；打开某节点历史即把该节点水位推进到**该节点最新记录的 id**。
- Service 逻辑（`latest_by_node`）：两条 `GROUP BY max(id)` —— 一条按 `node_id`（自身记录）、一条按 `parent_id`（仅 `action = delete`，即子节点删除记录计入父节点），合并取较大者。
- 响应 `200`：`{"latest": {"节点id": "记录id", ...}}`。整树级记录（`node_id` 为 NULL）不出现在 `latest` 里（没有对应节点行可挂红点）。
- **为什么返回记录 id 而不是时间**：记录 id 是时间有序的 UUIDv7（见 2.4），`max(id)` 即最新一条；`created_at` 只到秒，用户「点开历史」与「同一秒内又产生一条记录」相遇时，按时间比较会判成已读而漏掉红点，按 id 相等比较则不会。前端只做 `latest[node] === seen[node]` 的相等判断。
- 备注：小红点语义是「该用户没点开过」——已读状态按「项目 + 登录用户」存浏览器（localStorage），服务端不存个人已读；同一台机器换个账号登录、或换设备/清缓存，水位各算各的（本机没记过即视为没看过，会出点，可接受）。

### 5.11 GET /info-nodes/projects/{project_id}/marks —— 获取当前用户关注的节点 ID 列表

- 请求：无参数；**按当前登录人过滤**（Bearer token 的 `sub`，见 2.5 的按人隔离）。
- 用途：展示页进页面（及关注操作成功后）拉取，前端据此点亮「项目信息管理」卡里对应子节点的星标（`aria-pressed`）——点亮的是**自己**的关注。
- 响应 `200`：`{"node_ids": ["节点id", ...]}`；本人无关注返回 `{"node_ids": []}`。
- 错误：`401` —— 请求不带/带无效 token，识别不到登录人（关注列表是「每人一份」，没有身份就无法确定读谁的列表）。前端 api client 对 401 有刷新重试链路，token 过期可自愈。

### 5.12 POST /info-nodes/nodes/{node_id}/mark —— 切换当前用户的节点关注状态

- 请求：无 body。**幂等方向明确**：接口是「切换」——未关注→关注、已关注→取消（前端不需要传目标状态）。
- 人员：以 `get_request_actor_optional` 解析出的登录名（JWT `sub`）为准，**只写/删自己那一行**（主键 `node_id + operator`），别人对同一节点的关注不受影响。
- 响应 `200`：`{"marked": true|false}`（true = 切换后处于关注中）。
- 错误：节点不存在返回 `404`（前端树可能已过期——比如别人刚删了这个节点）；识别不到登录人返回 `401`（同 5.11）。
- 说明：星标只出现在**一级标签之下的子节点**上（前端行为，接口不限制层级——根节点调它也能成功、也会出现在动态里，只是 UI 没入口）。

### 5.13 GET /info-nodes/projects/{project_id}/activity —— 项目动态（当前用户被关注节点的最新变动）

- 请求：无参数；**只看当前登录人自己的关注**（同 5.11）。
- 用途：「项目动态」卡的数据源。**每个被关注节点只返回其最新一条变动**（来自 2.4 的 `project_info_node_change`，不另存一份变动），整体最近在前。
- Service 逻辑（`info_node_mark_service.marked_activity`）：先取当前人在该项目的关注 `node_id` 列表；按 `GROUP BY node_id + max(id)` 取各节点最新记录 id（记录 id 是时间有序的 UUIDv7，同秒靠它兜底），再按主键回查这 ≤N 条记录按 `created_at DESC, id DESC` 返回——不把全量历史拉回内存（查询量只随关注数增长，不随编辑次数增长）；节点/根节点标题用**当前**树的标题（记录里的 `node_title` 只是当时的快照）。上限 50 条（每节点至多一条，正常远达不到）。没记过任何操作的被关注节点不出现（无变动可展示）。
- 错误：`401` 同 5.11。
- 响应 `200`：

```json
{ "activity": [ { "node_id": "n1", "node_title": "客户信息", "root_title": "基础信息",
  "action": "update", "detail": "把内容从「空」改为「中力」", "created_at": "2026-09-16 14:42:13" } ] }
```

- 说明：**前端只展示 `root_title · node_title` + `detail`**，不展示 `created_at` 与人员（用户明确要求「只展示该节点的变动内容」）；`created_at` / `action` 仍返回，供将来扩展。`root_title` 沿当前树 `parent_id` 走到根（纯函数 `root_title_of`，链断/成环/超 32 层回退为节点自身标题）。取消关注后该节点的动态立即从动态里消失。

### 5.14 POST /info-nodes/projects/{project_id}/custom-nodes —— 增补信息（登录用户）

- 鉴权：**任何登录用户**（没有项目成员闸门）。它与 5.2 同性质（都给这棵树加本项目自己的节点），但 5.2 已在 2026-09-20 收紧为「本项目成员或 admin」，这一条没跟着动——是**既有口径**，不是本次的疏漏；要收紧只需给该路由挂 `require_project_member`（`tests/test_info_nodes_authz.py::test_custom_nodes_endpoint_is_still_login_only` 钉住了当前行为，改的时候改它而不是改坏它）。
- 请求体同 `InfoNodeCreate`，但 `parent_id` **必填**（`400 增补信息必须指定要挂在哪个节点下`），层级 ≤ 4（`PROJECT_INFO_MAX_DEPTH`），`node_key` 一律由服务端生成。
- 响应：`201`，新建节点。适用场景：普通用户在某节点下记一条表外信息。

### 5.15 GET / POST /info-nodes/template —— 详情模板（全局字段定义）

- 鉴权：模板权限码 `frontend:admin:project-info-template:show` 或 admin。该码不是人工勾的，而是 `permission_service._GLOBAL_ROLE_DERIVED_PERMISSIONS` 按**全局角色**（`user_project_roles.project_id IS NULL`）的名字「开发者 / 超级管理员」派生、随登录态下发的——后端 `require_permission` 与前端 `hasPermission` 读同一个码，两端判据不会漂。
- `GET`：返回全局字段定义（`project_info_node` 中 `project_id IS NULL` 的那部分）+ `name` / `updated_at` / `updated_by` / `project_count` / `source`（恒为 `db`）。前端**未持有该码时直接不发请求**。
- `POST`：保存全局字段定义，**保存即对全体项目生效**（项目不再持有节点副本，无需同步）。`dry_run=true` 只预览影响面；否则校验后更新/新增/停用全局节点行。模板里移除的字段是**停用**（`status='disabled'`）而非删除，项目已填的值留在 `project_info_value`，字段加回来即恢复。校验失败 → `400`。
- 与 5.2/5.14 的分界：改「全体项目共用的字段定义」走这里；改「我这个项目自己的树」走 5.2/5.14。节点级接口（5.3～5.5）碰到全局字段行一律 `403 全局字段定义请在「详情模板」里修改`。

### 5.16 GET /info-nodes/projects/{project_id}/ledger-sync —— 企业微信台账同步预览（**不落库**）

- 鉴权：与 5.8 同门槛——**该项目下的人**或 admin（要读整棵树，且会把项目信息回显给调用方）。
- 数据源：**本地 `project` 表里本项目那一行**——企业微信智能表格台账的同步镜像（平时由 `app/integrations/sources/wecom/adapter.py` 写进来，与项目列表页「项目经理」取的是同一张表的同一行）。不请求外部服务、不需要企业微信凭据，整个比对都在本地库完成，所以没有「台账服务不可达」这种失败态。
- Service 逻辑（`info_node_ledger_sync_service.build_sync_preview`，与 5.8 共用 `info_node_import_service` 的匹配内核）：
  1. 一个连接里读完：项目行（不存在 → `404 项目不存在`）+ 该行的台账值 + 展平后的信息树（无节点 → `400 该项目还没有信息节点…`）；
  2. 行 → 台账列：按模块里的 `PROJECT_LEDGER_FIELDS` 把字段名反查回台账列名（**与 adapter 的 `map_wecom_record_to_project` 一一对应，改台账列名要两处一起改**）。只收「原样落到字段上」的列——adapter 换过词的不进表（「项目类型」还落进 `category_basis`，那是 `CATEGORY_MAP` 换过的另一套词：普通项目 → 重要不紧急，加工过的值不是台账原文）；`status` 等于 adapter 对空列的兜底值 `待开始` 时跳过（那不是台账里的话）。定位列只剩 `项目名称`（= `project.name`，不是信息节点，不参与比对）；`项目编号` 2026-09-21 起按普通列参与比对——模板里新增了同名节点 `基础信息 / 项目编号`，台账编号要一并填进去（用户口径）；
  3. 台账列 → 条目（`title` = 列名，`value` = 文本，附件这类无文本的列跳过）。匹配先**按值认节点**：列的值正好等于某个可填下拉节点的一项（`_option_taker`，归一后精确相等，不走相似度与车型放宽）且**全树只有这一个**下拉装得下它时，这个条目就指到那个节点上——台账的称呼与信息树节点名常对不上（台账「项目生命周期」的值 `售前方案` = 节点「项目特性 / 时间线 / 大节点」的一项；「承接描述」的值 `中风险承接` = 「项目特性 / 风险点」的一项），值能对上就说明说的是同一件事，没有理由另建节点（用户口径「节点名不一致，但选项一样也可以归属为这个节点的信息」）。多个下拉装着同一项（车型1/2/3 同一套型号）或值只有 1 个字（`是`/`否` 满树都是）都不认，交回 `unmatched` 让用户决定；
  4. 再走内核的标题判等（归一后精确 + 相似度 ≥0.9 兜底、0.75～0.9 只认包含关系，与文件识别同一套 `match_items`）：台账列名要么对得上某个可填节点标题，要么不算匹配——台账是甲方口径的宽表，列名与节点名不同义的一律不猜（用户口径「匹配不到的先忽略」）。实测全量 297 个项目里这一档唯一够得着的就是「是否承接 → 是否对接」（恰好 0.75），被包含关系挡掉后台账侧结果与 0.9 口径逐条一致；**这里没有大模型参与**，也没有 5.8 的「建议归属附近的下拉」兜底（台账条目不带 `suggested_parent_path`，`_vicinity_dropdowns` 对空路径返回空）；
  5. 台账列名落在**分组节点**上时（同名，或列名是分组名去掉了限定词——`项目区域` ⊂ `项目区域/地点`），且该分组下**只有一个**装得下这个值的下拉子节点时，把值挂到那个下拉上（否则会新建一个与下拉各说各话的同名节点）；命中下拉但选项里没有这个值时，交给前端提示「先去编辑页补选项」而不是新建同名节点。这一步只处理前面按值没认走的条目（`_pin_option_values` 先跑，`_pin_group_values` 跳过已有归属的）；
  6. 分桶三组（与 5.8 同结构）：`fill`（节点为空）/ `overwrite`（节点有内容且与台账不同，即「矛盾」）/ `unmatched`（台账有、树里没有的列，附 `suggested_parent_path` 与 `note` 说明）；`unmatched` 的归属建议同样只按标题关系给——命中同名分组 → 挂到该分组下；相近的本身就是分组（`项目区域` vs `项目区域/地点`）→ 也挂到该分组下，并说明「在它下面新建」；标题互为包含关系（短的 ≥2 字且被长的包含）→ 挂到那个更具体的节点所在的层级，给不出关系就 `suggested_parent_id` 留空。
- 响应 `200`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `project_id` / `project_name` / `project_code` | string | 本项目 |
| `ledger_updated_at` | string \| null | 台账「更新时间」列（镜像落在 `project.recent_delivery_date`），前端展示数据新鲜度 |
| `field_count` / `mirror_field_total` | int | 本项目**有值**的台账列数（不含定位列）/ 镜像的台账列总数，让用户知道「台账还有多少列本项目没值」 |
| `fill` / `overwrite` | 数组 | 元素：`node_id, path, title, content_type, current, value`（`current` 为原内容，矛盾行靠它显示「原内容 → 新内容」） |
| `unmatched` | 数组 | 元素：`title, value, suggested_parent_id, suggested_parent_path, note`（`node_title`/`quantity` 恒为 null，台账没有车型那种「一款一条」的结构；`note` 是「为什么没匹配上」的说明，可为空） |

- 错误：`400`（项目还没有信息树）；`404`（项目不存在）。**没有 503**——数据源在本地库里。
- **本接口不写库**：与 5.8 一样由前端预览勾选后逐节点落库（fill/overwrite 走 5.3，unmatched 走 5.2，且只在给了 `suggested_parent_id` 时建）。默认勾选按入口不同：「同步信息」弹层**三组默认全勾**（用户口径「节点默认全选」，一步到位；没有改树权限时 `unmatched` 那组仍置灰不勾），「文件导入」仍是只默认勾 `fill`（识别可能有偏差，先填空的最保险）。
- **「同步」不新建一级标签**：与「文件导入」的差别只在这里——`unmatched` 里 `suggested_parent_id` 为空（树里既没有对应节点，也给不出相近的归类位置）的列，前端**置灰不勾、落库时跳过**，只作为「台账里有、树里还没有」的提醒留着，不再堆进「导入信息」兜底根节点（用户口径「不要新增根节点」）。兜底根节点只剩「文件导入」在用（`ProjectInfoImportPreview` 的 `allowFallbackRoot`，同步传 `false`）。

### 5.17 POST /info-nodes/projects/{project_id}/reset-to-template —— 一键清空（恢复为模板结构，项目成员）

- 功能：把本项目的信息树**恢复成模板的样子**（编辑页「同步」右侧的「一键清空」按钮）。
  返回 `{"cleared": <清掉的内容数>, "nodes_removed": <删掉的增补节点数>}`（用户口径「把整个项目的信息树清理成模板的结构，之前通过导入和同步增加的节点都要一起删掉」）。
- 鉴权：同 5.2/5.6——**该项目下的人**或 admin（`require_project_member`）。这一步会拆掉本项目的整片增补结构，页面按钮只是显眼，拦得住直连的是这里的闸门。操作人走 `get_request_actor_optional`，记进编辑历史。
- **删两样东西，其余一律不动**：

| 清掉 | 保留 |
|------|------|
| 本项目**增补的节点**（导入 / 同步 / 「增补信息」加进来的，`project_id` 非空，含其子孙）| 全局字段定义（模板本身，`project_id` 为空的行）及父子关系与排序 |
| 每个节点上的**值**（5.1 里各节点的 `value`，含挂在被删增补节点上的）| 下拉节点的**选项定义**（`options` 留着，只清 `selected`）|
| 增补节点上的「关注」标注（节点没了，星标点不开）| 全局字段上的关注（2.5）、其他人的关注、编辑历史（2.4）一条不删 |
| 节点对附件的挂载关系 | 附件文件本身（`resource_id` 只是解除挂载）|

  删完项目里剩下的就是全局模板结构、且都是空的——这就是「恢复成模板的样子」。旧 5.7「按模板重建」在新结构下等价于此，那个接口已废弃（全局定义全体项目共用，不存在逐项目补种）。
- Service 逻辑（`info_node_service.reset_to_template`）：查项目（软删除视为不存在 → `404`）→ 取本项目全部增补节点与值行 →
  ① 增补节点按**顶层子树**各写一条 `delete` 记录（挂在它原来的上级下，detail 说明带走了几个子节点，与 5.5 同口径）；
  ② 留在全局节点上的非空值逐条写 `delete` 记录（判空同首版：`None` / `''` / `[]` / `{}` 算空；`'0'`、`0`、`False` 不算空——**空串是值、0 也是值**）；
  ③ 加一条整树级记录（`node_id` 为 NULL）报两个数，项目级历史里一眼看到这次清空；
  ④ 清增补节点的关注标注 → 整批删值行与增补节点行（与 ①②③ 同一事务）。
  所有历史的 `change_reason` 一律 `一键清空`。**没填过的节点不写空转的历史**；项目本来就与模板一致（没值、也没增补节点）时连整树级那条也不写。
  `cleared` 只数**留下来的全局节点**上被清掉的内容（与「清空了内容」的历史条数一一对应）；挂在被删增补节点上的值随节点一起走，算进 `nodes_removed` 那一侧，不另记历史。
- 响应 `200 {"cleared": 0, "nodes_removed": 0}`：项目本来就与模板一致，不是错误（前端提示「本来就与模板一致，没有可清的内容」；动了东西时提示「已恢复为模板结构：删除 N 个增补节点、清空 M 项已填内容」）。
- 错误：`404`（项目不存在，含软删除）。
- **与 5.5 删除节点的区别**：5.5 是挑一棵增补子树删（结构操作，值随之没）；这里是「全删增补节点 + 清全部值」的一揽子恢复，一次把项目拉回模板形态。前端确认弹层文案：「清空本项目所有已填的内容，并删除导入/同步/增补加进来的节点，恢复成模板的样子」。

## 六、并发与一致性小结

1. **ext_info 并发编辑**：靠 `version` 乐观锁（冲突 409）+ 更新瞬间行锁串行化；内部系统写入不带 version，显式绕过乐观锁。
2. **信息树并发编辑**：逐节点独立 CRUD，天然缩小冲突粒度；移动/删除不做跨节点协同锁，后写覆盖先写。
2.1 **操作记录与业务同事务**：`add_change` 只 `db.add`，由调用方（info_node_service / info_template_service）统一 commit —— 业务失败即回滚，记录不会凭空多出；模板同步按项目逐个事务提交，某项目失败只影响该项目（记录也不会留下）。
2.2 **关注标注的清理也走同一事务**：`remove_marks` / `clear_project_marks` 接收调用方的 `db`（只 delete 不 commit），节点删除 / 整树替换与标注清理要么一起成功、要么一起回滚，不会出现「节点没了标注还在」。
3. **创建初始化的一致性**：project 插入与 info_nodes 初始化在同一 Session 内分两次 commit；模板实例化每节点生成独立 UUID，保证同源模板的多个项目不发生主键冲突。
4. **老数据兼容**：`ext_info` 为 NULL 的存量项目读取时按模板 lazy 填充（仅响应、不落库）；`version` 为 NULL 时按 1 参与校验与自增。

## 七、HTTP 错误码汇总

| 状态码 | 场景 | 来源 |
|--------|------|------|
| 400 | 更新节点时无任何有效字段；授权接口 type 参数非法；AI 摘要时信息树无节点；台账同步时项目还没有信息节点 | info-nodes / licenses / projects（ai-summary） |
| 401 | 未提供/无效 token、token 缺用户信息（/me 类接口）；关注/项目动态接口识别不到登录人（5.11～5.13，关注列表按人隔离） | projects、info-nodes |
| 403 | 不是这个项目的人动结构（5.2～5.6、5.8、5.16、5.17）：`只有该项目下的人员可以编辑项目信息树`；改全局字段定义（5.3～5.5）：`全局字段定义请在「详情模板」里修改`；没有模板权限码碰 `/template`：`权限不足` | info-nodes |
| 404 | 项目/节点不存在（含软删除项目）；对不存在的节点点关注（5.12） | projects、info-nodes |
| 409 | 项目编号/名称重复；**乐观锁版本冲突** | projects（PUT） |
| 422 | 请求体字段非法：`changes` 的 `limit` 越界（1～500）、`limit` 非整数 | info-nodes（changes） |
| 500 | 权限服务联动失败、删除项目外键残留等 | projects |
| 503 | 文件识别接口 / AI 项目摘要：`LLM_API_KEY` 未配置或大模型调用失败（detail 透传大模型给的中文原因） | info-nodes（parse-file）、projects（ai-summary） |

## 八、涉及文件清单

| 类型 | 文件 |
|------|------|
| 迁移 | [a7b8c9d0e1f2_add_project_ext_info.py](file:///d:/CODE/9_9/OpenRobotService/backend/alembic/versions/a7b8c9d0e1f2_add_project_ext_info.py)、[b2c3d4e5f6a7_add_project_info_node.py](file:///d:/CODE/9_9/OpenRobotService/backend/alembic/versions/b2c3d4e5f6a7_add_project_info_node.py)、[6f2c8a1d9b47_base_new_nodes_and_vehicle_rename.py](file:///d:/CODE/9_9/OpenRobotService/backend/alembic/versions/6f2c8a1d9b47_base_new_nodes_and_vehicle_rename.py)（2026-09-21：`基础信息` 下新增 项目编号/订单号/时间信息汇总（+5 子节点）、`硬件/车型信息` 下新增 总车数、`调度软件/版本` 下新增 版本号，并把「车辆」改名「车型信息」；存量库改模板只能靠迁移，见第三节） |
| 模型 | [app/models/delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py)（Project / ProjectInfoNode / ProjectInfoNodeChange / ProjectInfoNodeMark）、[models_das/models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/models_das/models.py)（再导出） |
| Schema | [schemas_das/request_models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/schemas_das/request_models.py) |
| API | [api/projects.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/projects.py)、[api/info_nodes.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/info_nodes.py) |
| Service | [services/project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py)、[services/info_node_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_service.py)、[services/info_node_import_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_import_service.py)、[services/project_ai_summary_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_ai_summary_service.py)（4.4）、[services/info_node_change_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_change_service.py)（2.4 / 5.9 / 5.10，另被 info_node_service、info_template_service 调用写记录；5.9 的子树口径 `list_for_subtree` 与纯函数 `subtree_node_ids` 也在这个文件里）、[services/info_node_mark_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_mark_service.py)（2.5 / 5.11～5.13，另被 info_node_service、info_template_service 调用清理标注）、[services/info_node_ledger_sync_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_ledger_sync_service.py)（5.16 企业微信台账同步预览：读本地 `project` 表镜像，复用 info_node_import_service 的 `match_items` 内核，自己不引大模型也不调外部服务，只做「台账列名/值 → 节点」的精确对齐 + 归属建议；按值认节点的 `_option_taker` / `_pin_option_values`、按列名的 `_pin_group_values`、未匹配的 `_enrich_unmatched` 都在这个文件里）、[services/info_node_seed_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_seed_service.py)（空库启动播种全局节点：`TITLE_KEY_MAP`（标题路径 → node_key）、`_seed_node_id`（标题路径 → 确定性 UUIDv5，改名节点走 `_ID_PATH_ALIASES` 按老路径推）、`build_seed_rows` / `ensure_global_info_nodes`；只在一个全局节点都没有时整棵写入） |
| 公共组件 | [app/core/llm_client.py](file:///d:/CODE/9_14/OpenRobotService/backend/app/core/llm_client.py)（backend 自维护的 LLM 客户端：DeepSeek/OpenAI 兼容非流式补全 + 网络重试，供 4.4 AI 摘要等 backend 大模型功能共用；密钥/模型与「文件识别」同源于 `settings`） |
| 测试 | [tests/test_info_nodes_authz.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_nodes_authz.py)（信息树写接口的鉴权闸门：项目成员放行/非成员全拦/节点级按归属项目判/全局字段放行给 Service/值写入与增补信息仍是登录即可/详情模板要权限码/**一键清空 5.17 同结构类门槛**，11 用例）、[tests/test_info_node_reset_to_template.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_node_reset_to_template.py)（一键清空 5.17 的鉴权与出参：成员放行且操作人记进历史、两个计数原样透传/非成员 403 且 Service 一次没被调/项目不存在 404/「空值」判据（`''`、`[]` 算空，`'0'`、`0`、`False` 不算），5 用例；删哪些行、记哪条历史由真库验证覆盖 `~/verify_reset_to_template.py`）、[tests/test_info_node_import.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_node_import.py)（文本抽取/目录与 prompt 构造/LLM 返回解析/标题模糊兜底 0.75 阈值（0.75～0.9 之间认包含关系、不认换字，0.75 以下不认）/select 选项匹配与「建议归属附近下拉」兜底/归属解析/车型分组与「总车数」路径解析（车型分组自己也叫「车型…」，别把它的父级当归属路径），32 用例）、[tests/test_project_ai_summary.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_project_ai_summary.py)（节点内容解码/信息树渲染/prompt 组装/输出清洗，10 用例）、[tests/test_info_node_change.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_node_change.py)（节点值→人话/逐字段变动文案/各操作类型文案（含 5.17 一键清空的整树级文案 `build_reset_detail`）/子树 id 先序（5.9 子树口径的纯函数，含已删根与成环兜底）/记录 id 时间有序，37 用例）、[tests/test_info_node_mark.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_node_mark.py)（根标题回溯/关注切换按人过滤（假 session）/标注清理，15 用例）、[tests/test_info_node_ledger_sync.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_node_ledger_sync.py)（台账同步 5.16：取值口径 / `project` 行 → 台账列还原（含 adapter 兜底值跳过）/ 空值与定位列过滤 / 按值指位（精确命中唯一的下拉选项；相似度、单字值、多候选都不认）/ 分组指位（同名与去限定词）/ 未匹配条目的归属建议与备注 / 整条预览的分桶与元信息（含按值认节点落到 `fill`、台账 `项目编号` 落到同名节点 `fill` 的用例），17 用例）、[tests/test_info_node_seed_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_node_seed_service.py)（`TITLE_KEY_MAP` 与 `default.yaml` 逐条对齐、播出树的层级/排序/类型、确定性 id 与开发库一致（改名节点按老路径推 id）、新节点的位置与序号、车型下拉目录、空库才播种，8 用例） |
| 模板 | [config/project_templates/default.yaml](file:///d:/CODE/9_9/OpenRobotService/backend/app/config/project_templates/default.yaml) |
| 路由挂载 | [modules/admin/__init__.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/__init__.py) |

## 九、前端对接要点

1. 打开项目详情时保存响应中的 `version`；编辑保存（PUT）原样带回，收到 409 提示刷新重试。
2. `ext_info` 按整体对象提交；信息大纲树不要放进 `ext_info`，改用 `/info-nodes/*` 逐节点操作。
3. 新建节点前由前端生成 UUID 作为 `id`；删除节点会连带删除整棵子树，需二次确认。
4. 拖拽节点后调 PATCH move；批量替换整树调 import（注意会先清空旧树）。
5. 空树项目一键初始化调 `import-template`——模板结构在后端 YAML 里，前端不保留副本（原前端常量 `PROJECT_INFO_TEMPLATE` 已删除），避免两套模板漂移。信息编辑页打开后若树为空会**自动**调它（无需点按钮），因此前端必须保证只触发一次（按项目 id 记忆，StrictMode 双跑 effect 也不能重发）。
6. 导入文件（`/import`）除节点数组外，也接受「标题 → 内容」紧凑映射（`""` 文字、`[...]` 下拉选项、`{...}` 子节点，即 `project_templates/tmp.json` 的写法）。
7. 信息编辑页「文件导入」走 `parse-file`（上传 → 转圈 → 三组预览勾选 → 确认后逐节点 CRUD）；上传时不要手写 `Content-Type`（交给浏览器带 boundary），大模型识别耗时较长，前端请求超时需放宽到 180s 以上。原「JSON 整树导入」入口已被该弹层替换，`/import` 接口与前端 `importInfoTreeApi` 保留未删（截图/调试仍可直调）。
8. 项目概况卡「项目摘要」（原「AI 项目摘要」）：非新建模式显示「点击生成 / 重新生成」按钮（生成中禁用），POST `/projects/{id}/ai-summary`（超时同样放宽到 180s）；摘要正文从 `project.ext_info.overview.ai_summary` 派生并用 react-markdown 渲染（结构化 Markdown；纯文本旧数据也兼容），生成响应里的 `ext_info` 直接替换本地状态即可持久展示；未生成过时展示「暂无数据」，空信息树项目后端会返回 400 提示先初始化信息树。
9. 编辑页每行的「历史」按钮：点开调 `GET /changes?node_id=X` 展示该节点记录（人员 / 操作类型 / 时间 / 具体变动，item 内字段见 5.9），文案直接展示 `detail`，不要前端再拼；操作人取 `operator_name || operator || '未知用户'`。**一级标签（根节点，`parent_id` 为 null）取的是整棵子树**（第 17 条）：请求带 `include_descendants=true&limit=200`，回来的是这一级下所有节点的记录，渲染成 Markdown 文档（`shared/utils/historyMarkdown.ts` 拼串 + react-markdown 渲染，动态文本转义）——一级标题「{根节点名} · 修改记录」，表头一行「共 N 条记录 · 涉及 M 个节点 · 时间范围」（记录数摸到 200 条时追加「已达显示上限（最近 200 条）」），正文按节点分组：小标题是该节点在树里的路径「根 / 子 / …」（顺序 = 树的先序，与编辑页行顺序一致），每条记录一行 `- **时间** 操作人 · 动作：变动`（动作中文名与列表共用 `HISTORY_ACTION_NAMES`）。节点已从树里删掉时单列一组「已删除 · {记录里的名称快照}」，整树级记录（`node_id` 为空）归「整棵信息树」；没有记录的节点不占一节。子节点仍是原来的逐条列表（只看自己那份）——两种口径下记录顺序都用后端给的，前端不重排。
10. 小红点：进页面、以及**每次保存成功后**各调一次 `GET /changes/summary`，与**本机**已读水位（localStorage `project-info-tree:history-seen:{项目code}:{登录用户}`，只存个人未读状态、不上传）比较——`latest[node] !== seen[node]` 或该用户没记过即出点；**只有点开过该节点历史才推进水位**（打开弹层时把该节点最新记录的 id 写入）。因此任何人保存节点后该节点立即出点、包括保存者自己——谁没点开过，谁就看得见红点；看过之后不再出点，直到有新记录。
11. 小红点还会**汇总到一级节点（根节点）**：某个下级节点有未读记录时，它所在的根节点同一位置也出点（中间层不出）。判定完全由前端根据第 10 条的未读集合往上归（按当前树的 parent_id 一路走到最外层，忽略已删除、树里没有对应行的节点），所以消失规则与子节点一致——点开某下级节点的历史后它不再贡献，该根节点下再没有别的未读变动时，根节点的点随之消失；根节点自己的记录没看过则仍然保留。服务端不需要为此加接口。
12. summary / changes 接口失败要静默（红点只是辅助提示，不打扰主流程），历史弹层本身失败则给出重试入口。
13. 展示页「项目信息管理」卡：一级标签**之下的子节点**右侧渲染星标（对照原型叶子星标，按用户口径落到全部子节点），进页面拉 `GET /marks` 点亮**自己**关注过的项（按人隔离，服务端按 token 过滤，前端无需传用户参数）；点击调 `POST /nodes/{id}/mark`（乐观翻转，失败回滚并 Toast），关注变化成功后通知外层刷新「项目动态」卡。响应体与共享版一致，前端每个登录人看到的列表各是各的。
14. 「项目动态」卡 = `GET /activity`：每条只渲染 `root_title · node_title`（与根同名时不重复拼前缀）+ `detail`，**不展示时间与人员**；无关注/无变动给空态引导（「去信息卡点星标」），加载失败给重试入口。
15. 项目详情页卡片裁剪（按用户要求）：只保留 项目概况 / 项目信息管理 / 项目动态 三张卡；原「项目生命周期」卡删除后**项目阶段**下拉挪进项目概况（仍可编辑）；基础画像 / 风险管理 / 责任体系等被删卡片的字段在前端本页不再有编辑入口（后端字段未动）。
16. 信息编辑页「同步」（`ProjectInfoLedgerSync.tsx`，入口在卡头操作区最左、`canEditTree` 才显示）走 5.16：弹层标题「同步信息」，小字为「基础信息比对：…」；打开即拉预览（转圈 → 三组勾选 → 确认后逐节点 CRUD，与 7 同款弹层，共用 `ProjectInfoImportPreview.tsx`）。前端约定：**三组默认全勾**（用户口径「节点默认全选」，落库前仍可逐条取消；共用组件用 `defaultCheckedAll` 开关，文件导入不受影响），`unmatched` 组在 `canEditTree=false` 时置灰且不默认勾（新建节点要改树结构）；预览取不到时（`400` 还没有信息节点 / `404` 项目不存在）在弹层里显示 detail 原文 + 「重试」，不留空预览。落库成功后外层刷新树与历史水位。**同步不新建一级标签**（用户口径「不要新增根节点，而是把台账和各节点及其下拉选项相匹配」）：共用组件加 `allowFallbackRoot`，文件导入 `true`（没归属的仍挂「导入信息」兜底根，行为不变）、同步 `false`——没归属的条目在预览里置灰、不默认勾、落库时跳过，行上写明「同步不新建一级标签，可到编辑页增补这类字段后再同步」，按钮数字也只算真能落库的行。
17. 根节点历史的子树口径（5.9 的 `include_descendants`）为什么必须带删除记录：按 2.4 的归属规则，删除是**整棵子树一条记录挂在被删节点的上级**上，被删的子孙自己已不在节点表里——只看 `node_id` 会把这一整片漏掉，所以后端按 `parent_id IN 子树 AND action=delete` 一并捞回。前端分组时这些记录的 `node_id` 在树里找不到，正好落进「已删除」组。已读水位不受影响：打开一级标签的历史时前端本来就把整棵子树标记已读（第 10、11 条）。**只读接口**：子树口径与 5.10 的红点一样走网关管控，不额外鉴权。
