# 测试分析报告：后台管理

> 基于 [PRD.md](../PRD.md)
---

后台管理」（管理视角）

> PRD 第八节 · Owner：产品经理 + 风险分析 Agent 工程师

### 3.1 功能点

| 编号 | 功能点 | 用户故事 |
|------|--------|---------|
| F19 | 管理员看板 | 查看工单统计概览 |
| F20 | 项目管理 | 管理项目 CRUD |
| F21 | 风险管理 | 识别和管理项目风险 |
| F22 | 日报生成 | 查看/生成项目日报 |
| F23 | 用户管理 | 管理平台用户 |
| F24 | 角色管理 | 管理角色和权限 |
| F25 | AI 风险分析 | 系统自动识别潜在风险 |
| F26 | 工单管理 | 查看/操作所有工单 |

### 3.2 业务流程

管理员登录 → 看板首页（工单统计概览）
→ 工单管理 / 项目管理 / 用户管理 / 日报管理
→ 项目关联 → 风险识别 → 风险分析 Agent 判定（红/黄/绿灯）

### 3.3 状态流转

项目状态机：planning → active → monitoring → completed / cancelled
风险状态机：identified → analyzing → mitigated → closed / escalated / false_alarm

### 3.4 权限控制

| 角色 | 能力 |
|------|------|
| 超级管理员 | 全部（含用户管理、角色管理） |
| 项目管理员 | F19-F22、F26（所属项目范围） |
| 普通管理员 | F19（仅看板查看） |

### 3.5 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/admin/tickets | GET | 工单管理列表 |
| /api/admin/tickets/stats | GET | 工单统计 |
| /api/admin/projects | GET/POST | 项目 CRUD |
| /api/admin/projects/{id} | GET/PUT/DELETE | 项目详情 |
| /api/admin/projects/risks | GET | 风险列表 |
| /api/admin/risks | GET/POST | 风险 CRUD |
| /api/admin/dashboard/tickets/summary | GET | 仪表盘汇总 |
| /api/admin/users/ | GET | 用户管理列表 |
| /api/admin/users/{id} | GET/PUT/DELETE | 用户管理 |
| /api/admin/roles/ | GET | 角色列表 |
| /api/admin/roles/{id} | GET/PUT | 角色管理 |

### 3.6 风险点

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 看板数据聚合查询性能 | P2 | 预聚合 + 定时刷新缓存 |
| 风险分析 Agent 误判率高 | P1 | 红黄灯阈值可配 + 人工确认 |
| 用户/角色权限配置不当 | P1 | 权限变更审计日志 |
| 日报内容偏离实际 | P2 | AI 生成 + 人工编辑 + 版本历史 |

### 3.7 边界条件

| 场景 | 预期行为 |
|------|---------|
| 非管理员访问后台接口 | 返回 403 |
| 删除正在被引用的用户 | 软删除 + 关联数据保留 |
| 项目删除前仍有未关闭工单 | 拦截删除 + 提示 |
| 看板数据为 0（新项目） | 显示空状态 + 引导操作 |

---

##
# 测试分析报告：后台管理

> 基于 [PRD.md](../PRD.md)

---

后台管理」（管理视角）

> PRD 第八节 · Owner：产品经理 + 风险分析 Agent 工程师

### 3.1 功能点

| 编号 | 功能点 | 用户故事 |
|------|--------|---------|
| F19 | 管理员看板 | 查看工单统计概览 |
| F20 | 项目管理 | 管理项目 CRUD |
| F21 | 风险管理 | 识别和管理项目风险 |
| F22 | 日报生成 | 查看/生成项目日报 |
| F23 | 用户管理 | 管理平台用户 |
| F24 | 角色管理 | 管理角色和权限 |
| F25 | AI 风险分析 | 系统自动识别潜在风险 |
| F26 | 工单管理 | 查看/操作所有工单 |

### 3.2 业务流程

管理员登录 → 看板首页（工单统计概览）
→ 工单管理 / 项目管理 / 用户管理 / 日报管理
→ 项目关联 → 风险识别 → 风险分析 Agent 判定（红/黄/绿灯）

### 3.3 状态流转

项目状态机：planning → active → monitoring → completed / cancelled
风险状态机：identified → analyzing → mitigated → closed / escalated / false_alarm

### 3.4 权限控制

| 角色 | 能力 |
|------|------|
| 超级管理员 | 全部（含用户管理、角色管理） |
| 项目管理员 | F19-F22、F26（所属项目范围） |
| 普通管理员 | F19（仅看板查看） |

### 3.5 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/admin/tickets | GET | 工单管理列表 |
| /api/admin/tickets/stats | GET | 工单统计 |
| /api/admin/projects | GET/POST | 项目 CRUD |
| /api/admin/projects/{id} | GET/PUT/DELETE | 项目详情 |
| /api/admin/projects/risks | GET | 风险列表 |
| /api/admin/risks | GET/POST | 风险 CRUD |
| /api/admin/dashboard/tickets/summary | GET | 仪表盘汇总 |
| /api/admin/users/ | GET | 用户管理列表 |
| /api/admin/users/{id} | GET/PUT/DELETE | 用户管理 |
| /api/admin/roles/ | GET | 角色列表 |
| /api/admin/roles/{id} | GET/PUT | 角色管理 |

### 3.6 风险点

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 看板数据聚合查询性能 | P2 | 预聚合 + 定时刷新缓存 |
| 风险分析 Agent 误判率高 | P1 | 红黄灯阈值可配 + 人工确认 |
| 用户/角色权限配置不当 | P1 | 权限变更审计日志 |
| 日报内容偏离实际 | P2 | AI 生成 + 人工编辑 + 版本历史 |

### 3.7 边界条件

| 场景 | 预期行为 |
|------|---------|
| 非管理员访问后台接口 | 返回 403 |
| 删除正在被引用的用户 | 软删除 + 关联数据保留 |
| 项目删除前仍有未关闭工单 | 拦截删除 + 提示 |
| 看板数据为 0（新项目） | 显示空状态 + 引导操作 |

---

##
