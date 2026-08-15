---
name: frontend-ui-migration
description: 将现有前端业务页面按照新的 UI 设计风格进行视觉与结构改造。核心原则是“保留原有页面、业务逻辑、接口和数据，只迁移新 UI 的视觉语言与交互表达”，禁止通过跳转、嵌入或新建独立页面绕过原有实现。
---

# Frontend UI Migration Skill

## 1. Skill 目标

当用户提供“新的 UI 截图 / 新设计稿 / Lovable 新生成页面”并要求改造现有前端时：

**必须基于现有前端页面进行改造，不得把新 UI 当成一个独立的新系统接入。**

目标是：

> **旧页面的业务能力 + 真实后端数据 + 新 UI 的视觉语言与布局**

最终效果应该让用户感觉：

- 还是原来的业务页面
- 原有功能仍然存在
- 原有接口仍然正常工作
- 原有数据仍然正常加载
- 只是页面被重新设计成新的 UI 风格
- 视觉上尽可能接近新的参考 UI

---

# 2. 核心原则

## 2.1 第一原则：禁止“新 UI 另起炉灶”

不得采用以下方式完成 UI 改造：

- 不得新建一个完全独立的新前端项目代替旧项目
- 不得通过 iframe 嵌入新的 UI
- 不得通过 iframe / webview / 外链加载新的 UI
- 不得让后端直接跳转到新的 UI
- 不得保留旧页面作为真实业务页面，再额外创建一个仅用于展示的新页面
- 不得通过修改路由，让原页面直接跳到新的独立页面
- 不得为了视觉效果而绕开原有 API、状态管理、权限控制和业务逻辑
- 不得为了快速实现而把真实数据替换成 Mock 数据

**必须修改现有页面本身。**

---

# 3. 修改优先级

按照以下优先级执行：

1. 现有业务功能不能丢失
2. 现有后端 API 和数据流不能被破坏
3. 原有权限、路由、状态、交互逻辑必须保持有效
4. 新 UI 的整体视觉风格必须迁移
5. 新 UI 的页面布局必须尽可能还原
6. 新 UI 的细节样式尽可能还原
7. 在不破坏业务的前提下优化响应式、可访问性和代码复用

如果“新 UI”与“原有业务逻辑”存在冲突：

**优先保证业务正确，再通过布局、组件拆分、信息层级等方式解决视觉冲突。**

---

# 3.5 重要例外：后台管理核心仪表盘的结构性重设计

对于“后台管理”页面中的核心仪表盘/数据看板，如果新 UI 不只是换颜色，而是对图表类型、指标布局、数据维度和交互方式进行了明显重设计，**必须按照新 UI 的设计重新实现该区域**。

此类场景不能只做 CSS 换肤，也不能因为旧页面已有组件就强行复用旧的图表结构。

核心原则：

> **保留原有业务数据语义、权限和业务目标；按照新 UI 重新设计展示层、组件结构和必要的数据接口。**

## 3.5.1 当前后台管理页的两个仪表盘属于结构性重设计

当前页面中两个核心区域：

1. **工单状态监测**
2. **跨项目看板**

原始页面与新 UI 的差异较大，因此需要以**新 UI 为最终目标**进行改造。

第一张截图/原始页面主要用于理解：

- 原来有哪些业务指标
- 原来有哪些统计数据
- 原来的业务含义
- 原来调用了哪些接口
- 哪些数据需要继续保留

第二张截图/新 UI 用于确定最终：

- 页面结构
- 图表类型
- 信息层级
- 指标布局
- 组件形态
- 颜色
- 字体
- 间距
- 圆角
- 交互方式

**最终实现不能被原页面的组件形态限制。**

---

## 3.5.2 工单状态监测：按新 UI 重新设计

如果原页面是“环形图 + 右侧数字 + 状态标签”的结构，而新 UI 变成：

- 新的蓝色系环形图
- 图例
- 每个状态的百分比
- 每个状态的数量
- 底部 KPI 指标
- 更轻量的 Card 布局

则应直接按照新 UI 重构整个组件。

不得只做：

```text
旧 DonutChart
    ↓
改颜色
    ↓
继续使用旧布局
```

而应该考虑：

```text
TicketStatusDashboard
├── StatusDonutChart
├── StatusLegend
├── StatusSummary
├── MetricCard
└── DetailLink
```

如果新 UI 要求的统计字段与旧接口不同，应检查 API，并在必要时增加或调整接口。

---

## 3.5.3 跨项目看板：按新 UI 重新设计

如果原页面使用项目阶段环形图，而新 UI 使用：

- 按月统计柱状图/趋势图
- 时间筛选器
- 同步最新数据按钮
- 项目总数
- 本月新增
- 风险项目
- 对接人缺管
- 项目紧急度分类卡片

这属于完整的数据看板重设计。

必须：

1. 按新 UI 重建看板布局。
2. 按新 UI 增加新的图表组件。
3. 增加时间筛选组件。
4. 增加同步/刷新组件。
5. 增加新的 KPI/指标卡。
6. 增加新 UI 所需的数据转换逻辑。
7. 检查并更新 API。
8. 新 UI 所需字段缺失时，新增后端统计接口或扩展现有接口。
9. 保持项目权限过滤和真实项目数据。

---

## 3.5.4 新 UI 与旧接口不一致时：允许换接口、扩展接口和新增接口

对于结构性仪表盘改造，**“不能破坏后端”不等于“绝对不能修改接口”。**

如果新 UI 所需数据与旧接口不匹配，应按以下优先级处理：

1. 优先复用现有真实字段。
2. 前端能够安全计算的派生指标，可以在前端计算。
3. 涉及数据库聚合、跨项目统计、权限过滤、大量数据计算时，新增专用统计接口。
4. 需要时扩展原接口字段，但必须检查其他调用方。
5. 不得使用 Mock 数据冒充真实接口。
6. 不得为了视觉效果硬编码截图中的数字。

例如可以形成：

```text
GET /dashboard/ticket-overview
GET /dashboard/project-trend
GET /dashboard/project-risk
```

具体路径和命名必须遵循现有项目 API 规范。

如果需要新增 API，应同时完成：

- 后端接口
- 数据查询/聚合
- 权限校验
- 参数校验
- 错误处理
- 前端 Service 调用
- Loading 状态
- Empty 状态
- Error 状态

---

## 3.5.5 图表变化必须伴随数据结构变化检查

不要把“更换图表”理解为仅替换 UI 组件。

例如：

```text
旧：project_status_count → DonutChart
新：monthly_project_count → BarChart
```

必须重新检查：

- X 轴数据
- Y 轴数据
- 时间维度
- 分类维度
- 聚合方式
- 总数计算
- 百分比计算
- Tooltip
- 图例
- 空数据
- 最大值/最小值
- 筛选条件

不能为了省事把旧数据结构原样塞进新图表。

---

## 3.5.6 新组件可以增加，且应保持组件化

当新 UI 需要旧项目没有的组件时，应增加组件，而不是把新 UI 强行塞进旧组件。

例如：

```text
Dashboard
├── TicketStatusChart
├── StatusLegend
├── MetricCard
├── TimeRangeSelector
├── SyncButton
├── ProjectTrendChart
└── UrgencySummary
```

组件拆分依据：

- 独立视觉模块
- 独立数据
- 独立交互
- 可复用性

避免两个极端：

```text
极端 1：整个 Dashboard 一个超大组件
极端 2：把每一行 JSX 都拆成组件
```

---

## 3.5.7 新 UI 截图中的数据仅作为设计参考

如果截图中显示：

```text
35
24
0
6%
```

这些数字默认视为**设计示例数据**，不能直接写死到生产代码。

实现时必须通过真实接口获得。

截图中的数字仅用于确定：

- 信息层级
- 数据展示格式
- 数字字号
- 数字颜色
- 图表结构
- 标签位置

---

## 3.5.8 结构性仪表盘的最终验收

除常规 UI 验收外，还必须检查：

- [ ] 两个仪表盘均以新 UI 为最终设计目标
- [ ] 图表类型与新 UI 一致
- [ ] 新 UI 所需组件均已实现
- [ ] 必要接口已经新增/扩展
- [ ] 真实后端数据已接入
- [ ] 统计口径明确
- [ ] 项目权限过滤正确
- [ ] 时间筛选正确
- [ ] 同步/刷新功能正确
- [ ] Tooltip/图例/百分比/数量正确
- [ ] Loading 状态正确
- [ ] Empty 状态正确
- [ ] Error 状态正确
- [ ] 不存在为了适配新 UI 而创建的平行新页面
- [ ] 不存在 Mock 数据替代真实数据
- [ ] 原页面仍然通过原有业务入口访问

---

# 4. 开始修改前：必须先分析现有项目

在真正修改代码之前，先检查：

## 4.1 项目结构

识别：

- 技术栈
- 路由系统
- 页面目录
- 公共组件
- UI 组件库
- CSS / Tailwind / CSS Modules / styled-components 等样式体系
- 状态管理
- API 请求封装
- 数据模型
- 权限控制
- 国际化（如存在）
- 主题系统（如存在）

## 4.2 找到对应旧页面

必须确认：

- 新 UI 对应的是哪个旧页面
- 旧页面入口是什么
- 旧页面有哪些子组件
- 旧页面调用了哪些 API
- 旧页面有哪些关键业务状态
- 哪些组件是公共组件，修改后会影响其他页面

不要在没有定位旧页面的情况下直接开始重写。

---

# 5. 新 UI 分析方法

拿到截图/设计稿后，不要只看“长得像不像”。

必须拆解成以下视觉设计系统：

## 5.1 页面级

识别：

- 页面整体宽度
- 内容区域最大宽度
- 页面左右留白
- Header / Sidebar / Topbar 结构
- 页面背景色
- 内容区域背景色
- 页面整体层级
- 页面分区方式

## 5.2 色彩

尽可能识别：

- 主色
- 辅助色
- 背景色
- 卡片背景色
- 边框色
- 分割线颜色
- 一级文字颜色
- 二级文字颜色
- 禁用文字颜色
- 成功 / 警告 / 错误色
- hover / active / selected 状态颜色

不要只修改按钮颜色。

应建立统一的视觉 Token。

例如：

```text
--color-primary
--color-bg
--color-surface
--color-border
--color-text-primary
--color-text-secondary
--color-success
--color-warning
--color-danger
```

如果项目使用 Tailwind，应尽量复用或扩展主题 Token，而不是在每个组件里写大量独立颜色。

---

# 6. 尺寸和空间必须重点迁移

需要重点检查：

- 页面宽度
- 最大内容宽度
- Header 高度
- Sidebar 宽度
- Card 高度
- Card 最小高度
- Button 高度
- Input 高度
- Table 行高
- 标签高度
- Icon 大小
- Section 间距
- Card 间距
- 页面左右 Padding
- 上下 Margin
- 元素之间的 Gap

尤其注意：

**不要只“颜色像”，但整体空间比例完全不同。**

视觉还原中：

> 间距、尺寸、比例的重要性不低于颜色。

---

# 7. 字体和文字层级

尽可能匹配新 UI：

- 字体族
- 字重
- 字号
- 行高
- 字间距
- 大小写形式
- 文字颜色
- 标题层级

至少区分：

```text
Page Title
Section Title
Card Title
Body
Secondary
Caption
Label
Button
```

如果截图无法精确判断字号，使用视觉比例推断，不要随意全部使用同一个字号。

---

# 8. Card / Container 设计

重点迁移新 UI 的卡片语言：

- 圆角大小
- 阴影
- 边框
- 背景
- 内边距
- 标题区域
- 内容区域
- Footer 区域
- Card 与 Card 的间距
- Card 内元素排列

注意：

**圆角、阴影、边框和内边距必须统一。**

例如不能出现：

```text
Card A: 8px
Card B: 12px
Card C: 20px
```

除非参考 UI 本身明确存在层级差异。

---

# 9. 标签、Badge、Status 样式

如果新 UI 使用状态标签，需要迁移：

- 背景色
- 文字颜色
- 边框
- 圆角
- 高度
- Padding
- 字号
- 字重
- Icon
- 状态语义

保持状态语义不变。

例如：

```text
进行中
已完成
已暂停
高风险
待处理
```

不能因为改 UI 而改变后端 status 值。

可以改变展示层：

```text
backend status
    ↓
status mapping
    ↓
new UI Badge
```

---

# 10. 页面布局必须尽量一致

重点检查新 UI 中各区域的位置：

- Sidebar 在哪里
- Header 在哪里
- 页面标题在哪里
- 操作按钮在哪里
- 搜索框在哪里
- 筛选区域在哪里
- Tab 在哪里
- 主卡片在哪里
- 数据表在哪里
- 弹窗从哪里出现
- Drawer 从哪里出现
- 空状态在哪里
- 分页在哪里

**不要只复制组件，而忽略组件之间的空间关系。**

如果参考 UI 是：

```text
Title
    ↓
Summary Cards
    ↓
Filter
    ↓
Table
```

不要最终实现成：

```text
Title + Filter
    ↓
Table
    ↓
Summary Cards
```

除非原有业务约束明确要求如此。

---

# 11. 保留现有后端和业务逻辑

这是本 Skill 的硬性要求。

不得轻易修改：

- API URL
- HTTP Method
- 请求参数
- Response 数据结构
- 数据类型
- 鉴权逻辑
- 用户权限逻辑
- 项目权限逻辑
- 状态机
- 业务校验
- 后端接口调用顺序

除非用户明确要求修改。

如果只是 UI 改造：

**优先修改 Presentation Layer，而不是 Service / API Layer。**

推荐结构：

```text
API / Service
      ↓
State / Data
      ↓
Existing Page Logic
      ↓
New UI Components
      ↓
New Visual Style
```

而不是：

```text
New UI
   ↓
重新造一套 Mock 数据
   ↓
绕过原后端
```

---

# 12. 数据和 UI 解耦

对于原页面已经存在的数据：

- 保留真实 API
- 保留真实 loading
- 保留 error
- 保留 empty state
- 保留 pagination
- 保留 sorting
- 保留 filtering
- 保留 optimistic update（如存在）
- 保留刷新逻辑

仅修改数据显示方式。

例如：

```text
旧：

<div className="old-card">
    task.name
</div>

新：

<NewTaskCard>
    task.name
</NewTaskCard>
```

而不是：

```text
const tasks = mockTasks
```

---

# 13. 组件重构策略

优先考虑：

> **复用业务组件 + 重构展示结构**

而不是：

> 删除旧页面 → 新写一套页面

可以：

- 重构组件结构
- 拆分 Card
- 拆分 Header
- 拆分 Filter
- 拆分 Table
- 拆分 Status Badge
- 提取通用 Button
- 提取通用 Modal
- 提取通用 Empty State
- 提取通用 Section

但必须确保：

**业务行为不变。**

---

# 14. 建立 Design Token

如果新 UI 风格比较统一，应提取：

```text
Color
Spacing
Radius
Shadow
Typography
Component Height
Border
Icon Size
```

例如：

```css
:root {
  --ui-primary: ...;
  --ui-bg: ...;
  --ui-surface: ...;
  --ui-border: ...;

  --ui-radius-sm: ...;
  --ui-radius-md: ...;
  --ui-radius-lg: ...;

  --ui-space-1: ...;
  --ui-space-2: ...;
  --ui-space-3: ...;
  --ui-space-4: ...;

  --ui-shadow-sm: ...;
  --ui-shadow-md: ...;
}
```

如果项目已有 Design System：

**优先扩展现有 Design System，不要重复创建另一套。**

---

# 15. 图标和图片

新 UI 中如果存在图标：

- 优先寻找项目已有图标库
- 保持 icon 风格一致
- 保持 stroke / fill 一致
- 保持大小一致
- 保持 icon 与文字间距一致

不要随意混用：

- Lucide
- Heroicons
- Font Awesome
- Emoji
- SVG

如果参考 UI 使用统一图标风格，应尽量统一。

图片同理：

- 尺寸
- 比例
- 圆角
- 裁切方式
- object-fit
- 占位图

需要统一。

---

# 16. 交互状态必须一起迁移

UI 改造不能只考虑默认状态。

必须检查：

- hover
- active
- selected
- focus
- disabled
- loading
- error
- empty
- success
- expanded
- collapsed
- modal open
- drawer open
- dropdown open

例如 Card 不仅要做：

```text
默认状态
```

还需要考虑：

```text
hover
active
selected
disabled
```

这样新 UI 才会真正完整。

---

# 17. 响应式设计

必须检查：

- Desktop
- Laptop
- Tablet
- Mobile（如项目支持）

尤其检查：

- 卡片是否换行
- Grid 是否变化
- Sidebar 是否折叠
- Table 是否横向滚动
- Button 是否压缩
- Text 是否溢出
- Modal 是否超出屏幕
- 页面左右间距是否合理

不得为了还原截图而把页面写死。

---

# 18. 可用性要求

视觉还原不能牺牲基本 UX：

- 点击区域不能过小
- 文本不能难以阅读
- 对比度不能严重不足
- 操作按钮必须易于识别
- 页面层级要清晰
- 信息密度要合理
- 表单错误要可识别
- Loading 状态要明显
- 空数据要有明确反馈

---

# 19. 修改流程

每次 UI 改造按以下流程：

## Step 1：分析旧页面

输出：

```text
页面路径：
核心组件：
API：
状态：
主要业务功能：
公共组件：
```

## Step 2：分析新 UI

输出：

```text
整体布局：
颜色：
字体：
尺寸：
间距：
Card：
Button：
Input：
Badge：
Table：
Modal：
Sidebar：
Header：
```

## Step 3：建立 UI 映射

例如：

```text
旧页面组件        → 新 UI 表现

Old Header         → New Header
Old Summary Card   → New Summary Card
Old Filter         → New Filter
Old Table          → New Table
Old Status Badge   → New Badge
```

## Step 4：优先改 Design Token

先统一：

- 颜色
- 字体
- Radius
- Shadow
- Spacing
- Component Size

## Step 5：改页面布局

再调整：

- Grid
- Flex
- Width
- Height
- Position
- Gap
- Padding
- Margin

## Step 6：改组件

再处理：

- Card
- Button
- Input
- Table
- Badge
- Modal
- Dropdown
- Tabs

## Step 7：验证业务

确认：

- API 正常
- 数据正常
- 权限正常
- 路由正常
- 提交正常
- 删除正常
- 查询正常
- 筛选正常
- 分页正常

## Step 8：视觉检查

必须对比新 UI：

```text
颜色
尺寸
字体
间距
布局
比例
圆角
阴影
组件位置
```

---

# 20. 修改代码时的保护规则

修改前：

**先阅读代码，再修改。**

不要：

- 直接覆盖整个文件
- 删除未知业务代码
- 删除已有 API
- 删除状态逻辑
- 删除权限判断
- 删除异常处理
- 删除 loading
- 删除 empty state
- 删除用户操作逻辑

除非确认这些代码属于旧 UI 且确定可以替换。

如果一个组件同时包含：

```text
业务逻辑 + UI
```

优先把：

```text
业务逻辑
```

保留下来，再替换：

```text
UI Structure
```

---

# 21. 防止过度重构

UI 改造不等于技术重构。

除非用户要求：

- 不要更换框架
- 不要更换 UI Library
- 不要更换状态管理
- 不要更换 API Client
- 不要更换路由方案
- 不要大规模移动目录
- 不要升级大量依赖

**先完成视觉迁移，再考虑技术重构。**

---

# 22. 视觉验收标准

完成以后，至少检查：

## Layout

- [ ] 页面整体宽度一致
- [ ] Header 高度接近
- [ ] Sidebar 宽度接近
- [ ] 内容区域位置一致
- [ ] Card 排列方式一致
- [ ] 组件相对位置一致

## Color

- [ ] 页面背景一致
- [ ] Card 背景一致
- [ ] 主色一致
- [ ] 文字颜色层级一致
- [ ] Border 一致
- [ ] Status 颜色一致

## Typography

- [ ] 标题字号接近
- [ ] 正文字号接近
- [ ] Label 字号接近
- [ ] 字重接近
- [ ] 行高接近

## Shape

- [ ] Card 圆角一致
- [ ] Button 圆角一致
- [ ] Input 圆角一致
- [ ] Badge 圆角一致
- [ ] Shadow 接近

## Spacing

- [ ] 页面 Padding 接近
- [ ] Card Padding 接近
- [ ] Section 间距接近
- [ ] Grid Gap 接近
- [ ] 文本与 Icon 间距接近

## Function

- [ ] API 正常
- [ ] 页面加载正常
- [ ] 数据正常
- [ ] 权限正常
- [ ] 搜索正常
- [ ] 筛选正常
- [ ] 分页正常
- [ ] 新增正常
- [ ] 编辑正常
- [ ] 删除正常
- [ ] 弹窗正常
- [ ] 错误状态正常
- [ ] 空状态正常

---

# 23. 完成标准

只有同时满足以下条件，才能认为 UI 改造完成：

### A. 业务不变

原有业务能力仍然可用。

### B. 后端不绕行

继续使用真实后端 API。

### C. UI 完成迁移

新 UI 的：

- 配色
- 尺寸
- 字体
- 字号
- 标签
- Card
- 圆角
- 阴影
- 间距
- 页面布局
- 元素位置
- 交互状态

均完成迁移。

### D. 没有重复页面

不得出现“旧页面 + 新页面”两个版本并存来规避改造难题。

### E. 没有 Mock 替代真实数据

除非用户明确要求。

---

# 24. 最终输出格式

完成修改后，用简短结果汇报：

```text
本次 UI 改造完成：

1. 保留了原有后端 API 和业务逻辑
2. 基于原页面进行了 UI 改造，没有创建独立的新页面
3. 已迁移：
   - 配色
   - 字体
   - 字号
   - Card
   - 圆角
   - 间距
   - 布局
   - Badge
   - Button
4. 已检查：
   - Loading
   - Empty
   - Error
   - Responsive
   - API 数据
5. 未修改：
   - 核心业务逻辑
   - 权限体系
   - API 契约
```

如果发现新 UI 与旧业务存在无法直接兼容的地方，必须明确指出：

```text
问题：
原因：
当前旧逻辑：
建议的 UI 适配方案：
是否需要用户确认：
```

不要私自改变业务逻辑。
