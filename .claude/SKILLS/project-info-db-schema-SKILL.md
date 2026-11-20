---
name: project-info-db-schema
description: >
  用于修改 AGV 交付平台“项目信息管理”数据库结构。适用于将现有 JSON 项目信息模型改造成“全局模板节点 + 项目独立值 + 项目自定义节点 + 值修改历史”的可配置树形数据模型，并生成 ORM、Migration、数据迁移及一致性校验。
---

# Project Info DB Schema Skill

## 1. 适用场景

当任务涉及以下内容时使用本 Skill：

- AGV 交付平台“项目信息管理”数据库改造
- 将现有项目详情 JSON 转换为数据库树形节点
- 管理员维护全局项目字段模板
- 每个项目保存自己的字段值
- 项目字段值需要修改历史
- 用户可以增加仅属于当前项目的自定义字段
- 项目自定义字段需要支持树形层级
- 需要使用现有项目 ORM / Migration 机制进行数据库改造

本 Skill 的核心目标不是“直接把 JSON 存进数据库”，而是建立以下关系：

```text
全局节点 = 公共字段定义
项目值   = 某个项目对某个节点的实际数据
自定义节点 = 某个项目额外拥有的字段定义
历史     = 某个项目某个节点的值变化记录
```

---

# 2. 核心业务规则

必须遵守以下规则：

1. 只有一套全局模板，不做模板版本管理。
2. 全局节点由所有项目共享，但全局节点本身不保存项目实际值。
3. 全局节点使用 `project_id = NULL` 表示。
4. 项目实际值必须通过 `project_id + node_id` 关联。
5. 同一个全局节点可以被多个项目使用，每个项目拥有独立值。
6. 项目 A 修改全局节点对应的值，不得影响项目 B。
7. 项目自定义节点使用 `project_id = 当前项目ID` 表示。
8. 项目自定义节点只能被所属项目使用。
9. 项目自定义节点允许挂载在全局节点下面。
10. 项目自定义节点可以继续创建自己的子节点，形成多层树。
11. 用户只能修改字段值；任何节点下都能新增项目自定义字段（「增补信息」），最多 4 层。
12. 管理员可以维护全局节点：新增、修改、停用、排序、移动、配置字段类型等。
13. 管理员停用全局节点时优先软删除，不物理删除。
14. 全局模板不保存项目值。
15. 项目默认不需要预创建全部空值，只保存实际填写过的值。
16. 项目自定义字段不会自动沉淀回全局模板。
17. node_id 是节点唯一身份，不承担排序职责。
18. sort_order 负责同级节点排序。
19. node_name 只负责前端展示，不能作为程序逻辑唯一标识。
20. node_key 是稳定的程序标识，节点改名时原则上不改变。
21. 项目值修改和修改历史写入必须处于同一个数据库事务。
22. 敏感凭据不得以明文写入历史表和日志。

---

# 3. 推荐数据模型

优先复用现有项目结构。如果不存在可复用模型，再新增：

```text
project
    │
    ├── project_info_value
    │         │
    │         └── project_info_value_history
    │
    └── project_info_node
```

其中 `project_info_node` 同时保存：

```text
project_id = NULL
    → 全局模板节点

project_id = A
    → 项目 A 的自定义节点
```

核心关系必须成立：

```text
全局节点：
project_info_node
project_id = NULL
node_id = 101
node_key = base.customer_info
node_name = 客户信息

项目 A：
project_info_value
project_id = A
node_id = 101
value_json = "XX科技"

项目 B：
project_info_value
project_id = B
node_id = 101
value_json = "YY物流"

项目 A 自定义节点：
project_info_node
project_id = A
node_id = 201
node_key = custom.dock_number
node_name = 月台编号

项目 A 自定义值：
project_info_value
project_id = A
node_id = 201
value_json = "A-03"
```

必须理解为：

```text
node = 字段定义
value = 项目实际数据
project_id = 数据属于哪个项目
node_id = 数据对应哪个字段
```

---

# 4. 全局节点与项目值的关联规则

这是本 Skill 最重要的数据库语义。

## 4.1 全局节点

全局节点：

```text
project_info_node.project_id IS NULL
```

例如：

```text
node_id = 101
node_key = base.customer_info
node_name = 客户信息
project_id = NULL
```

它只表达：

> 所有项目都存在“客户信息”这个字段。

它绝不能保存：

```text
XX科技
YY物流
```

这些值必须位于 `project_info_value`。

## 4.2 全局节点的多项目值

同一个全局节点可以对应多个项目值：

```text
project A + node 101 → XX科技
project B + node 101 → YY物流
project C + node 101 → null
```

因此：

```text
node_id 相同
project_id 不同
value 不同
```

这是合法且必须支持的。

## 4.3 项目自定义节点

如果：

```text
project_info_node.id = 201
project_info_node.project_id = A
```

则只有项目 A 可以拥有该节点的值：

```text
project_info_value.project_id = A
project_info_value.node_id = 201
```

以下情况必须被拒绝：

```text
project_info_node.project_id = A
project_info_value.project_id = B
project_info_value.node_id = 201
```

必须在数据库约束能力允许的情况下尽量保证；如果数据库无法直接表达该条件，则在 Service / Repository 层强制校验。

---

# 5. `project_info_node` 设计

建议字段：

```text
id
project_id
parent_id
node_key
node_name
node_type
value_type
sort_order
required
allow_custom
config
status
created_by
created_at
updated_by
updated_at
```

## 5.1 id

节点永久身份。

- 自增主键或沿用现有 ID 方案
- 不用于排序
- 不因为插入中间节点而变化
- 不因为拖拽排序而变化

## 5.2 project_id

节点作用域：

```text
NULL      = 全局模板节点
非 NULL   = 对应项目的自定义节点
```

这是区分公共模板和项目自定义字段的关键。

## 5.3 parent_id

表示父节点。

例如：

```text
基础信息
├── 客户信息
├── 订单信息
└── 项目区域地点
```

可以保存为：

```text
基础信息.parent_id = NULL

客户信息.parent_id = 基础信息.id
订单信息.parent_id = 基础信息.id
项目区域地点.parent_id = 基础信息.id
```

重要：

- `parent_id` 不是序号。
- 中间增加节点不会导致其他节点 id 改变。
- 调整层级只修改 `parent_id`。
- 如果已有项目自定义节点挂载在全局节点下，移动全局节点时必须谨慎处理其子节点关系。

## 5.4 node_key

稳定的程序标识，例如：

```text
base.customer_info
base.order_info
network.remote.ssh.ip
network.remote.ssh.port
```

要求：

- 节点改名不应随意修改 node_key。
- 不用中文 node_name 做逻辑判断。
- 同一作用域内 node_key 不重复。
- 不依赖名称定位字段。

## 5.5 node_name

前端展示名称，例如：

```text
客户信息
订单信息
公网IP
SSH
端口
```

## 5.6 node_type

至少：

```text
root
group
field
```

如现有系统已有枚举，优先复用。

## 5.7 value_type

至少考虑：

```text
text
number
boolean
date
select
multi_select
person
attachment
json
```

优先兼容现有实现。

## 5.8 sort_order

仅控制同级节点顺序。

推荐：

```text
10
20
30
40
```

插入中间节点可以使用：

```text
15
```

拖拽后也可以重新归一化顺序。

不要使用 id 排序。

## 5.9 required

```text
0 = 否
1 = 是
```

## 5.10 allow_custom

表示用户是否可以在当前节点下增加项目自定义字段。

> **该闸门已于 2026-09-18 取消**（用户要求「所有节点都默认可以增加」）：
> 「详情模板」页的「允许各项目在此节点下增补信息」勾选框删除，`add_custom_node`
> 不再读这一列。列仍保留在表里（`add_custom_node` / `import_tree` 新建节点、
> `normalize_template_nodes` 保存模板一律写 `true`；迁移 `5b8e3f2a9c47` 把存量 0
> 补齐），但它只是「此位置允许增补」这一事实的记录，不参与任何判断。

因此**任何节点下都能增补**，唯一的边界是层数：**最多 4 层**（`MAX_INFO_DEPTH`，
第 5 层起 400「信息层级最多 4 层」）。项目自己增补出来的节点同样可以接着往下增补。

勾选框时代的旧口径（已废弃，仅作历史参考）：只有模板勾过的位置能加，
```text
基础信息 → true
硬件 → true
```
不勾的节点下不能创建项目自定义字段。

## 5.11 config

JSON，用于保存字段配置。

例如：

```json
{
  "options": [
    {
      "value": "haixun",
      "label": "海研"
    },
    {
      "value": "honeywell",
      "label": "霍尼韦尔"
    },
    {
      "value": "xiango",
      "label": "仙工"
    }
  ]
}
```

## 5.12 status

建议：

```text
active
disabled
```

管理员“删除”全局节点时优先：

```text
status = disabled
```

不要直接物理删除，以保证历史项目值和历史记录仍可关联。

---

# 6. 节点唯一性

需要保证：

## 全局节点

在全局作用域内：

```text
node_key 唯一
```

## 项目自定义节点

在同一项目范围内：

```text
project_id + node_key 唯一
```

不同项目可以有相同 node_key：

```text
项目 A + custom.dock_number
项目 B + custom.dock_number
```

是合法的。

如果使用 MySQL，需要注意：

```text
UNIQUE(project_id, node_key)
```

无法单独可靠地表达“全局 project_id=NULL 时必须唯一”的业务语义。

请结合实际数据库版本和 ORM 能力设计合适索引或 Service 层校验。

---

# 7. `project_info_value` 设计

建议：

```text
id
project_id
node_id
value_json
created_at
updated_at
updated_by
```

核心唯一关系：

```text
UNIQUE(project_id, node_id)
```

语义：

> 一个项目对于一个节点只有一份当前值。

建议：

```text
project_id → project.id
node_id → project_info_node.id
```

在现有数据库规范允许时建立 Foreign Key。

## 7.1 value_json

推荐 JSON 存储：

文本：

```json
"1.2.3"
```

数字：

```json
22
```

多选：

```json
["大客户项目", "POC项目"]
```

日期：

```json
"2026-09-17"
```

人员：

```json
{
  "user_id": 123,
  "name": "张三"
}
```

复杂对象：

```json
{
  "ip": "10.10.10.10",
  "port": 22
}
```

## 7.2 不预创建全部空值

如果模板有 200 个字段，项目不需要创建 200 条空记录。

只有用户实际填写后才创建：

```text
project_info_value
```

前端无记录时显示空值。

---

# 8. `project_info_value_history` 设计

建议：

```text
id
project_id
node_id
old_value
new_value
operation_type
changed_by
changed_at
change_reason
```

至少支持：

```text
create
update
delete
```

历史必须包含：

```text
project_id
+
node_id
```

不能只保存 node_id。

因为：

```text
node 101
```

可能分别有：

```text
项目 A 的历史
项目 B 的历史
项目 C 的历史
```

## 8.1 事务要求

“更新当前值”和“写历史”必须放在同一事务：

```text
BEGIN

更新 project_info_value

写入 project_info_value_history

COMMIT
```

任何一步失败都回滚。

---

# 9. 全局模板不做版本管理

禁止引入：

```text
template_v1
template_v2
template_v3
```

也不要给 project 增加 template_version_id。

当前逻辑：

```text
一套全局模板
+
所有项目实时使用当前模板
```

管理员修改：

```text
project_id = NULL 的节点
```

即可。

项目已有的 `project_info_value` 不需要迁移或复制。

例如：

```text
全局节点
node_id = 101
node_name = 客户信息
```

管理员改名为：

```text
客户名称
```

应当：

```text
node_id 不变
node_key 不变
node_name 改变
```

已有项目值完全不变。

---

# 10. 项目自定义字段

项目可以增加：

```text
基础信息
├── 客户信息      ← 全局节点
├── 订单信息      ← 全局节点
└── 月台编号      ← 项目自定义节点
```

项目自定义节点：

```text
project_id = A
```

所以：

```text
项目 A 可见
项目 B 不可见
项目 C 不可见
```

项目自定义字段可以形成自己的层级：

```text
特殊设备
├── 品牌
├── 型号
└── 通讯协议
```

这些节点全部：

```text
project_id = A
```

子节点通过 `parent_id` 连接。

---

# 11. 全局节点与项目自定义节点可以混合组成树

允许：

```text
全局：
基础信息
├── 客户信息
├── 订单信息
└── 项目区域地点
```

项目 A：

```text
基础信息
├── 客户信息
├── 订单信息
├── 项目区域地点
└── 月台编号
```

其中：

```text
基础信息 = 全局节点
月台编号 = 项目 A 自定义节点
```

`月台编号.parent_id` 可以直接指向全局“基础信息”节点。

这意味着：

> 项目自定义节点可以挂载在全局节点下面，但不会修改全局节点本身。

---

# 12. 前端读取数据的标准逻辑

查询当前项目时，需要得到：

```text
所有 active 全局节点
+
当前项目 active 自定义节点
```

逻辑：

```text
node.project_id IS NULL
OR
node.project_id = 当前项目ID
```

再把项目值挂上去：

```text
LEFT JOIN project_info_value value
  ON value.project_id = 当前项目ID
 AND value.node_id = node.id
```

最终数据结构应类似：

```text
基础信息
├── 客户信息 = XX科技
├── 订单信息 = PO20260917
├── 项目区域 = 上海
└── 月台编号 = A-03
```

其中：

```text
客户信息 / 订单信息 / 项目区域
→ 全局节点 + 当前项目 value

月台编号
→ 当前项目自定义节点 + 当前项目 value
```

注意：

```text
node.project_id = NULL
```

只能说明：

> 这是全局字段定义。

不能解释成：

> 这个字段没有项目数据。

项目数据必须从：

```text
project_info_value.project_id
```

判断。

---

# 13. 管理员模板编辑能力

数据库层需要支持：

```text
新增节点
修改节点
停用节点
调整排序
调整父节点
修改字段类型
修改是否必填
修改 allow_custom
修改 config
```

调整位置：

```text
只修改 parent_id / sort_order
```

不要修改：

```text
node_id
node_key
```

除非是重新定义了字段。

管理员对全局节点的操作：

```text
project_id = NULL
```

不应该直接修改任何项目的：

```text
project_info_value
```

---

# 14. 敏感字段

重点注意：

```text
SSH密码/验证码
ToDesk账号
ToDesk密码
AnyDesk密码
服务器账号
服务器密码
公网连接凭据
```

不得将敏感值明文写入：

```text
project_info_value_history.old_value
project_info_value_history.new_value
```

也不得在应用日志中打印完整敏感值。

优先复用现有项目的：

- 加密存储
- 脱敏
- 权限控制
- Secret/Credential 机制

如果项目暂无成熟方案，不要擅自设计一套新的认证体系；保留扩展点，并明确 TODO。

---

# 15. 现有 JSON 模板迁移

现有结构类似：

```json
{
  "项目信息管理预设信息": {
    "基础信息": {
      "客户信息": "",
      "订单信息": "",
      "工单信息": "",
      "项目区域地点": {}
    },
    "硬件": {},
    "IT端软件": {},
    "调度软件": {},
    "网络信息": {},
    "服务器部署": {},
    "环境": {},
    "业务系统": {},
    "业务流程": {},
    "人员信息": {},
    "项目特性": {},
    "项目配置": {},
    "项目定制": {}
  }
}
```

迁移要求：

1. 对象节点 → `group`
2. 叶子节点 → `field`
3. 正确建立 `parent_id`
4. 设置合理的 `value_type`
5. 保留中文 node_name
6. 生成稳定 node_key
7. 正确处理重复字段名，例如“拆包”
8. 不丢失已有项目数据
9. 全局模板节点：
   ```text
   project_id = NULL
   ```
10. 已有项目数据：
   ```text
   project_info_value.project_id = 原项目ID
   project_info_value.node_id = 对应节点ID
   ```
11. 不得把项目实际值写回全局 node。

迁移前先检查现有数据库到底如何保存项目 JSON/项目信息，再决定数据迁移策略。

---

# 16. Migration 工作流程

执行前必须：

1. 检查现有项目 ORM。
2. 检查数据库类型和版本。
3. 检查 Migration 工具。
4. 搜索现有项目详情相关 Model。
5. 搜索当前 JSON 模板定义。
6. 搜索已有项目数据的存储位置。
7. 搜索现有 API 对这些数据的依赖。
8. 判断哪些表可以复用。

然后再实施。

优先使用现有：

```text
SQLAlchemy / Alembic
Django Migration
Prisma
TypeORM
Drizzle
```

等项目原生方式。

不要直接修改线上数据库。

需要提供：

```text
ORM Model 修改
Migration
数据迁移脚本
索引
Foreign Key（如项目规范允许）
回滚方案
```

---

# 17. 兼容性要求

禁止：

- 破坏现有 project 表
- 无依据删除旧字段
- 影响现有工单
- 影响用户
- 影响权限
- 改坏已有项目数据
- 为同一业务创建重复 Model/表
- 在未阅读现有代码前直接重构

优先兼容现有：

```text
project_id
user_id
created_by
updated_by
created_at
updated_at
```

的字段类型和命名。

遵循现有数据库和 ORM 规范。

---

# 18. 必须通过的验收测试

## Case 1：同一个全局节点对应多个项目值

全局：

```text
node_id = 101
project_id = NULL
node_name = 客户信息
```

项目 A：

```text
project_id = A
node_id = 101
value = XX科技
```

项目 B：

```text
project_id = B
node_id = 101
value = YY物流
```

必须同时存在。

---

## Case 2：项目 A 修改全局节点值

项目 A：

```text
XX科技 → XX科技有限公司
```

项目 B 仍然：

```text
YY物流
```

不能互相影响。

---

## Case 3：项目 A 新增自定义字段

```text
月台编号 = A-03
```

项目 B、C 不可见。

---

## Case 4：项目 A 新增自定义层级

```text
特殊设备
├── 品牌
├── 型号
└── 协议
```

必须正确保存：

```text
project_id
parent_id
node_id
```

并且项目 B 不可见。

---

## Case 5：历史隔离

同一个全局节点：

```text
node_id = 101
```

项目 A 和 B 都修改过。

查询：

```text
project_id = A
```

只能得到项目 A 历史。

查询：

```text
project_id = B
```

只能得到项目 B 历史。

---

## Case 6：管理员修改全局节点名称

```text
客户信息 → 客户名称
```

必须：

```text
node_id 不变
node_key 不变
node_name 修改
```

已有项目值保持不变。

---

## Case 7：管理员停用全局节点

```text
公网IP → status = disabled
```

当前页面不展示，但：

```text
project_info_value
project_info_value_history
```

仍然保留。

---

## Case 8：调整模板顺序

```text
客户信息
订单信息
工单信息
```

调整为：

```text
订单信息
客户信息
工单信息
```

只修改：

```text
sort_order
```

不修改：

```text
node_id
```

---

## Case 9：非法项目值关联

禁止：

```text
node.project_id = A
value.project_id = B
value.node_id = node.id
```

必须被拦截。

---

# 19. 执行策略

不要一上来就修改代码。

严格按照下面顺序：

```text
Step 1
读取现有数据库 Model / Migration

Step 2
确认当前项目信息 JSON 的真实存储方式

Step 3
确认现有 project / user / permission 表结构

Step 4
设计最终数据库关系

Step 5
修改 ORM Model

Step 6
生成 Migration

Step 7
如果存在旧数据，编写数据迁移

Step 8
执行本地 Migration

Step 9
运行数据库一致性测试

Step 10
验证项目 A / B 隔离

Step 11
验证全局节点和项目自定义节点

Step 12
输出最终变更报告
```

如果发现当前项目已有相同用途的表，优先修改和复用，不要重复创建。

---

# 20. 最终报告格式

完成后必须汇报：

## 现状

当前项目原来的相关数据结构是什么。

## 变更

新增/修改了哪些 Model / Table。

## 关系

明确说明：

```text
project
    ↓
project_info_value.project_id

project_info_node
    ↓
project_info_value.node_id
```

并明确说明：

```text
全局节点：
project_info_node.project_id = NULL

项目值：
project_info_value.project_id = 具体项目
```

## 历史

说明：

```text
project_info_value_history
```

如何保存历史。

## Migration

列出执行的 Migration 和数据迁移情况。

## 测试

列出实际执行的测试。

## 风险

指出兼容性、数据迁移、索引、敏感字段等风险。

---

# 21. 最终不可违背的核心规则

```text
全局节点：
project_info_node
project_id = NULL
node_id = 101
node_key = base.customer_info

项目 A 的值：
project_info_value
project_id = A
node_id = 101
value = "XX科技"

项目 B 的值：
project_info_value
project_id = B
node_id = 101
value = "YY物流"

项目 A 自定义节点：
project_info_node
project_id = A
node_id = 201
node_key = custom.dock_number

项目 A 自定义值：
project_info_value
project_id = A
node_id = 201
value = "A-03"
```

必须始终保持：

```text
node = 字段定义
value = 项目实际数据
project_id = 数据属于哪个项目
node_id = 数据对应哪个字段
```

其中：

```text
全局 node 可以被多个项目引用
项目自定义 node 只能被所属项目引用
不同项目的 value 永远互相隔离
```

不要实现“项目自定义字段自动沉淀回全局模板”。
