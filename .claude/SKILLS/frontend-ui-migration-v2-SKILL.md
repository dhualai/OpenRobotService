---
name: frontend-ui-migration
description: 将现有真实业务前端按照新的 UI 设计进行迁移和重构。适用于用户提供新 UI 截图、Lovable 生成页面、设计稿等场景。核心要求是保留原有业务能力、真实数据和接口体系，并在原页面基础上完成新 UI 的视觉与结构迁移；对于新 UI 新增的业务展示模块，允许先做前端占位和 Mock/静态展示，但必须明确标记为待后端接口接入，不得伪装成真实数据。
---

# Frontend UI Migration Skill

## 1. 任务目标

当用户提供“原有业务页面”和“新的 UI 页面/截图”，要求把原页面改造成新 UI 时，目标不是简单换肤，也不是重新创建一个独立页面。

目标是：

> 以原有前端为业务基础，以新 UI 为最终设计目标，保留原有真实业务能力，并完成视觉、布局、组件和交互层的迁移。

最终结果必须满足：

- 原有页面入口保持有效
- 原有真实后端业务继续工作
- 原有权限、路由、状态、业务校验继续有效
- 新 UI 的整体视觉语言被迁移到原页面
- 新 UI 的页面结构、布局和组件尽可能还原
- 新 UI 新增模块可以先呈现前端结构
- 后端暂时没有接口的部分，不阻塞前端 UI 开发，但必须使用清晰的占位/待接入状态
- 不得为了实现视觉效果而破坏现有业务逻辑

---

# 2. 总原则：新 UI 是目标，原页面是业务基础

必须建立以下认知：

```text
原页面
= 理解“原来有什么业务、数据、接口、权限、操作”

新 UI
= 决定“最终页面长什么样、信息如何组织、组件怎么呈现、交互如何表达”
```

因此：

> 原页面不是最终视觉标准，新 UI 才是最终视觉标准。

不要因为旧页面以前使用某种组件，就认为新页面必须继续使用它。

例如：

```text
旧：
环形图 + 标签列表

新：
柱状图 + 时间筛选 + KPI 卡片
```

那么可以直接将旧展示结构改成新结构，而不是为了“保留旧代码”继续使用旧图表。

---

# 3. 严格禁止的实现方式

以下方式均视为错误实现：

### 3.1 不得新建一个独立的新业务页面

不能：

```text
旧项目详情页
      ↓
新建一个 NewProjectDetailPage
      ↓
后端跳转到 NewProjectDetailPage
```

如果需求是修改原项目详情页，必须修改原页面。

### 3.2 不得 iframe / webview / 外链嵌入

不能通过：

- iframe
- webview
- 外链页面
- 嵌入 Lovable 页面

来规避前端迁移。

### 3.3 不得绕过原后端

不能：

- 为了新 UI 自己创建第二套接口体系
- 直接从浏览器访问数据库
- 把后端逻辑复制到前端
- 用 Mock 永久代替真实接口

### 3.4 不得因为 UI 改造而删除业务能力

不能因为新设计没有明显展示某个字段，就直接删除：

- API 请求
- 权限判断
- 数据校验
- 操作逻辑
- 状态逻辑
- 错误处理
- Loading
- Empty State

如果新 UI 暂时没有展示某业务，先确认是否真的可以移除。

---

# 4. 三类 UI 改造必须区分

## 类型 A：纯视觉迁移

例如：

- 换颜色
- 换字体
- 换字号
- 换圆角
- 换阴影
- 换按钮样式
- 换 Card 样式
- 换间距

这种情况：

> 不应改变 API 和业务逻辑。

---

## 类型 B：结构重排

例如：

- 原来的左右布局改成上下布局
- 原来多个区域合并成一个 Card
- 操作按钮位置改变
- 字段重新分组
- 原有信息模块重新排序

这种情况：

> 可以大幅修改页面 JSX / Template 结构，但业务数据和 API 尽量保持。

---

## 类型 C：业务展示能力新增

例如：

- 新 UI 增加“项目工单”
- 新 UI 增加“项目动态”
- 新 UI 增加趋势图
- 新 UI 增加时间筛选
- 新 UI 增加同步数据按钮
- 新 UI 增加 AI 摘要
- 新 UI 增加修改历史

这种情况：

> 前端必须按新 UI 设计新增对应模块和组件。

后端有接口：

```text
直接接真实接口
```

后端没有接口：

```text
先完成前端结构
+
明确显示“暂无数据 / 待接入”
+
不要阻塞整体 UI 开发
+
不要凭空制造看起来像真实业务的数据
```

---

# 5. 当前重点案例：项目详情页四大模块

当页面被重新设计为：

```text
项目详情页
├── 项目概况
├── 项目信息管理
├── 项目工单
└── 项目动态
```

必须以这四个模块作为最终页面的信息架构。

---

## 5.1 项目概况

项目概况属于：

> 原有业务能力 + 新 UI 布局优化 + 部分新增字段/输入框。

要求：

- 保留原有项目基础信息
- 保留原有 API
- 按新 UI 重新布局
- 按新 UI 调整信息层级
- 新增 UI 中的输入框、编辑入口、状态展示等必须呈现
- 已有接口能够支持的字段直接接入
- 没有后端接口但新 UI 已要求展示的字段，可以先做前端控件和占位状态

例如：

```text
项目名称
项目编号
客户信息
项目经理
对接人
项目进度
部署时间
近期交付
最终交付
AGV 数量
USP 版本
AI 项目摘要
```

这些字段不要因为旧页面没有而拒绝实现。

---

## 5.2 项目信息管理

这是一个新增的完整业务模块。

必须按新 UI 实现：

- 模块标题
- 编辑按钮
- 展开/收起
- 信息分类
- 分类标签
- 信息字段
- 字段值
- 未填写状态
- 输入/编辑能力
- 保存/取消状态（如果设计存在）
- 与项目概况之间的视觉层级关系

如果后端已有对应字段：

```text
直接读取真实数据
```

如果没有：

```text
先完成前端字段展示和交互占位
```

不得因为“后端没有接口”而删除新 UI 中的字段。

推荐前端状态：

```text
已有数据
未填写
暂未接入
待后端支持
```

不要使用看起来像真实业务数据的假值。

---

## 5.3 项目工单

项目工单是新增模块。

前端需要优先实现完整展示框架，例如：

```text
项目工单
├── 工单统计
├── 工单筛选
├── 工单列表
├── 状态
├── 优先级
├── 类型
├── 提单人
├── 处理人
├── 创建时间
├── 更新时间
└── 最晚解决时间
```

如果已有工单 API：

```text
直接接入真实 API
```

如果暂时没有“按项目查询工单”的接口：

```text
前端先完成：
- 模块布局
- 筛选控件
- Table / List
- 状态 Tag
- 空数据状态
- Loading
- 分页结构
```

并显示：

```text
暂无项目工单数据
```

或：

```text
该模块正在接入数据
```

不要为了让页面“看起来完整”而硬编码工单。

---

## 5.4 项目动态

项目动态是新增模块。

目标是形成：

> 项目的时间线 / Activity Feed / 操作记录。

可呈现：

- 项目信息修改
- 工单创建
- 工单状态变化
- 项目阶段变化
- 项目负责人变更
- 项目成员变更
- 项目进度变化
- AI 摘要生成
- 项目同步
- 重要业务操作

前端组件建议：

```text
ProjectActivityTimeline
ActivityItem
ActivityActor
ActivityTime
ActivityType
ActivityDetail
```

如果暂时没有后端动态接口：

- 先完成时间线组件
- 先完成空状态
- 不得虚构真实用户操作记录
- 可以使用明确标记的开发占位数据，仅用于 UI 开发验证
- 一旦进入真实业务联调，必须替换成真实 API 数据

---

# 6. 接口策略：允许“先 UI、后 API”

这是本 Skill 的重要特殊规则。

当新 UI 已经确定，但后端接口还没有完成时：

> **前端不需要等待后端再开始。**

采用：

```text
UI Design
   ↓
Frontend Component
   ↓
Data Adapter / Interface
   ↓
真实 API（已有）
       或
占位数据源（暂无 API）
```

建议为数据访问抽象一层：

```text
projectService
ticketService
projectActivityService
projectInfoService
```

例如：

```ts
getProjectDetail()
getProjectTickets()
getProjectActivities()
updateProjectInfo()
```

当接口不存在时，可以先：

```ts
getProjectActivities()
```

返回明确的 empty state 或开发占位数据。

未来只替换 service：

```text
Mock / Placeholder
       ↓
Real API
```

而不要重写整个页面组件。

---

# 7. Mock 数据的使用规则

允许临时 Mock，但必须满足：

### 可以

- 开发阶段验证 UI
- 验证图表
- 验证列表布局
- 验证时间线
- 验证组件状态

### 不可以

- 把 Mock 当生产数据
- 把 Mock 数据写死在页面 JSX
- 让用户误认为数据是真实数据
- 用 Mock 掩盖后端接口不存在的问题

推荐：

```text
page
 ↓
service
 ↓
data source
```

而不是：

```text
page
 ↓
const data = [...]
```

---

# 8. 新 UI 视觉迁移要求

新 UI 与原 UI 存在差异时，必须尽量按照新 UI 调整：

## 8.1 配色

检查：

- 页面背景
- Card 背景
- 主色
- 辅助色
- 一级文字
- 二级文字
- 辅助文字
- Border
- Divider
- 成功
- 警告
- 错误
- Disabled
- Hover
- Active

新 UI 如果使用蓝色主视觉，不得只改按钮颜色。

整个页面都要形成统一的蓝色视觉体系。

---

## 8.2 尺寸

重点检查：

- 页面宽度
- 最大内容宽度
- Header
- Sidebar
- Card
- Button
- Input
- Table
- Tag
- Icon

不能只做到“颜色一样”，但尺寸比例完全不同。

---

## 8.3 字体

尽量匹配：

- Font Family
- Font Weight
- Font Size
- Line Height
- Letter Spacing
- 标题层级
- 数字字体层级

---

## 8.4 Card

尽量还原：

- 圆角
- 阴影
- Border
- Padding
- Header
- Content
- Footer
- 卡片间距

---

## 8.5 间距

重点还原：

```text
Page Padding
Section Gap
Card Gap
Card Padding
Text Gap
Icon Gap
Button Gap
```

新 UI 通常最容易因为间距错误而产生明显差异。

---

## 8.6 标签

需要统一：

- 高度
- Padding
- 字号
- 字重
- Border Radius
- Background
- Border
- Icon

---

# 9. 编辑状态 / 输入框

项目概况和项目信息管理中，如果新 UI 出现：

- Input
- Select
- Date Picker
- Textarea
- Editable Text
- Inline Edit

需要区分：

```text
展示态
编辑态
保存中
保存成功
保存失败
```

不要只做一个 Input 放在那里。

如果后端接口暂时不存在：

- 控件仍然呈现
- 可以允许本地编辑
- 保存按钮可以处于 disabled / 待接入状态
- 或明确提示“该字段暂未开放保存”

不要伪装成已经成功保存到后端。

---

# 10. 组件设计

建议按业务模块拆分：

```text
ProjectDetail
├── ProjectOverview
│   ├── ProjectHeader
│   ├── ProjectBasicInfo
│   ├── ProjectProgress
│   ├── ProjectSchedule
│   └── ProjectAISummary
│
├── ProjectInfoManagement
│   ├── InfoCategorySelector
│   ├── InfoSection
│   ├── InfoField
│   └── InfoEditForm
│
├── ProjectTickets
│   ├── TicketSummary
│   ├── TicketFilter
│   ├── TicketList
│   └── TicketStatusTag
│
└── ProjectActivity
    ├── ActivityTimeline
    └── ActivityItem
```

实际项目结构应根据代码仓现状调整。

原则：

> 独立的视觉、数据或交互模块应抽成组件。

---

# 11. 数据层与 UI 层分离

推荐：

```text
API
 ↓
Service
 ↓
Mapper / Adapter
 ↓
View Model
 ↓
UI Component
```

特别是新 UI 和旧 API 字段不一致时：

```text
后端：
project_name

前端：
projectName
```

应该通过 mapper 处理：

```text
API Response
      ↓
mapProject()
      ↓
ProjectViewModel
      ↓
UI
```

不要让整个 UI 代码充满：

```text
data.xxx.yyy.zzz
```

---

# 12. API 改造规则

## 已有 API 能满足

直接复用。

## 已有 API 不完全满足

可以：

- 扩展字段
- 增加查询参数
- 增加聚合接口
- 增加统计接口

但必须先检查调用方。

## 没有 API

可以先完成：

```text
UI + Component + Service Interface + Empty State
```

等后端接口完成后再接入。

## 禁止

- 修改 API 造成其他页面崩溃
- 删除旧接口
- 绕过权限
- 前端直接访问数据库
- 永久使用 Mock
- 在页面组件里直接写 HTTP 细节

---

# 13. 权限

所有项目详情数据都必须继续遵循原有权限体系。

尤其是：

- 项目访问权限
- 项目成员权限
- 项目信息编辑权限
- 工单查看权限
- 工单操作权限
- 项目动态查看权限

新 UI 不得因为新增页面模块而绕过权限。

例如：

```text
旧页面只能看到用户有权限的项目
```

新 UI 仍然必须如此。

---

# 14. Loading / Empty / Error / Disabled 必须完整

每个新模块至少考虑：

### Loading

```text
加载中
Skeleton
Spinner
```

### Empty

```text
暂无数据
暂无工单
暂无动态
信息尚未填写
```

### Error

```text
加载失败
重新加载
```

### Disabled / Pending API

当后端接口还没有接入时：

```text
该功能暂未接入
```

不要出现：

```text
成功保存
```

之类虚假反馈。

---

# 15. 截图里的数据不是生产数据

用户提供的新 UI 截图中的：

```text
项目名称
编号
人数
日期
工单数量
动态记录
```

默认均视为**设计示例**。

不得把截图里的数据硬编码成生产数据。

截图只用于：

- 布局
- 视觉
- 信息层级
- 数据格式
- 组件形态

真正数据必须来自真实 API。

---

# 16. 修改流程

在写代码前必须先完成以下分析：

## Step 1：识别现有页面

```text
路由：
入口：
主要组件：
主要 API：
状态管理：
权限：
```

## Step 2：识别新 UI

```text
页面结构：
模块：
颜色：
字号：
布局：
组件：
新增字段：
新增交互：
```

## Step 3：建立数据映射表

至少确认：

| 新 UI 模块 | 所需数据 | 已有接口 | 是否需要新增接口 | 当前状态 |
|---|---|---|---|---|
| 项目概况 | 项目基础信息 | 有 | 否 | 接入 |
| 项目信息管理 | 分类字段 | 部分有 | 视情况 | 部分接入 |
| 项目工单 | 项目工单 | 部分/无 | 视情况 | 前端先做 |
| 项目动态 | Activity | 无/部分 | 视情况 | 前端先做 |

## Step 4：先建立组件结构

不要一上来堆 JSX。

先确定：

```text
Page
 ↓
Sections
 ↓
Components
 ↓
States
```

## Step 5：优先修改 Design Token

统一：

- Color
- Typography
- Radius
- Shadow
- Spacing
- Component Size

## Step 6：调整页面结构

完成：

- 模块顺序
- Grid
- Flex
- 宽高
- Padding
- Gap
- Position

## Step 7：接数据

按照：

```text
已有 API
 ↓
真实数据

没有 API
 ↓
Placeholder / Empty State
```

## Step 8：补齐交互状态

包括：

- Hover
- Active
- Selected
- Loading
- Empty
- Error
- Disabled
- Editing
- Saving
- Saved

## Step 9：视觉验收

逐项对比截图：

```text
颜色
大小
字体
布局
间距
圆角
阴影
标签
组件位置
```

---

# 17. 特别注意：不要“为了保持旧逻辑而抵抗新设计”

错误思路：

> “旧页面已经有一个组件，所以新 UI 也只能在这个组件上改。”

正确思路：

> “旧页面提供业务能力，新 UI 提供最终表现形式。”

因此允许：

```text
旧 Card → 新 Card
旧 Table → 新 List
旧 Donut → 新 Bar
旧 Tabs → 新 Selector
旧详情布局 → 新分区布局
```

只要业务语义和真实数据正确即可。

---

# 18. 特别注意：也不要“为了新设计而过度重构”

UI 改造不是无边界重构。

除非用户明确要求：

- 不要更换前端框架
- 不要更换路由
- 不要更换状态管理
- 不要大规模升级依赖
- 不要重写整个项目
- 不要重新建立另一套 Design System

优先：

> 局部重构 + 组件复用 + 数据层解耦。

---

# 19. 最终验收标准

## 页面结构

- [ ] 项目概况符合新 UI
- [ ] 项目信息管理已增加
- [ ] 项目工单已增加
- [ ] 项目动态已增加
- [ ] 模块顺序正确
- [ ] 页面层级正确

## 业务

- [ ] 项目真实数据正常
- [ ] 原有 API 正常
- [ ] 原有权限正常
- [ ] 原有操作正常
- [ ] 新增接口正确接入
- [ ] 暂无接口的模块没有伪造真实业务数据

## UI

- [ ] 配色接近新 UI
- [ ] 字体接近
- [ ] 字号接近
- [ ] Card 接近
- [ ] 圆角接近
- [ ] 阴影接近
- [ ] 标签接近
- [ ] 间距接近
- [ ] 元素位置接近
- [ ] 响应式合理

## 状态

- [ ] Loading
- [ ] Empty
- [ ] Error
- [ ] Disabled
- [ ] Editing
- [ ] Saving
- [ ] Success

均有合理处理。

---

# 20. 最终输出格式

完成开发后，用以下格式汇报：

```text
本次项目详情页 UI 改造已完成。

一、页面结构
- 项目概况：已按新 UI 调整布局，并增加新的输入/编辑展示
- 项目信息管理：已新增
- 项目工单：已新增
- 项目动态：已新增

二、接口接入
- 已接入接口：
  xxx
  xxx

- 暂无接口：
  xxx
  xxx

三、前端占位
暂无接口的模块已完成：
- 页面结构
- 组件
- Loading / Empty / Error 状态
- 后续可直接替换数据 Service 接入真实 API

四、业务保护
- 未通过新页面绕过原有页面
- 保留原有权限
- 保留原有业务逻辑
- 保留真实 API

五、视觉迁移
- 配色
- 字体
- 字号
- Card
- 圆角
- 阴影
- 间距
- 布局
- 组件
均已按照新 UI 调整。

六、待后端事项
明确列出尚未具备 API 的数据项，不自行假设接口已经存在。
```

如果发现业务逻辑与新 UI 无法直接兼容，必须先报告：

```text
冲突点：
旧业务逻辑：
新 UI 要求：
技术影响：
推荐方案：
```

未经用户确认，不得私自改变关键业务规则。
