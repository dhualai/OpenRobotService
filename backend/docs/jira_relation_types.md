# Jira Issue Link 关系类型分析

## 一、Jira 标准 Issue Link 类型

Jira 内置 7 种 Issue Link（不含 Sub-task 层级关系，Sub-task 是独立类型，不走 Issue Link）。

| Jira 关系 | 主动方含义 | 被动方含义 | 方向 |
|-----------|-----------|-----------|------|
| **is blocked by** | 当前工单被对方阻塞（对方未完成我不能进行） | 对方阻塞当前工单 | → 指向前置 |
| **blocks** | 当前工单阻塞对方（我未完成对方不能进行） | 对方被当前工单阻塞 | → 指向后置 |
| **clones** | 当前工单是对方的克隆（拷贝） | 对方是当前工单的源单 | → 指向源单 |
| **is cloned by** | 对方是当前工单的克隆 | 当前工单是对方的源单 | → 指向克隆 |
| **duplicates** | 当前工单与对方重复（我标记对方为重复） | 对方被当前工单标记为重复 | → 指向被标记方 |
| **is duplicated by** | 对方标记当前工单为重复 | 当前工单被对方标记为重复 | → 指向标记方 |
| **relates to** | 一般关联，无方向语义 | 同左 | 双向 |

### 方向说明

Jira 的 Issue Link 永远是 **有向边**（A → B），每种关系都有 inward/outward 两个标签。
在 API 返回里 `inwardIssue` 是"指向我的"，`outwardIssue` 是"我指向的"。

## 二、与本系统现有关系类型对比

本系统（OpenRobotService）目前定义的三种关系：

| 本系统关系 | 语义 | 对应 Jira | 备注 |
|-----------|------|-----------|------|
| **subtask** | 子任务（层级） | Sub-task（非 Issue Link） | 父 → 子，层级归属 |
| **predecessor** | 前置依赖 | `is blocked by`（inward）+ `blocks`（outward） | 合并为一种双向依赖关系 |
| **duplicate** | 重复 | `duplicates` + `is duplicated by` | 合并为一种双向重复标记 |

### 差异项

| Jira 关系 | 本系统是否支持 | 建议 |
|-----------|--------------|------|
| `clones` / `is cloned by` | ❌ 未支持 | 可归入 duplicate（克隆 ≈ 复制，语义接近），或单独加 `clone` |
| `relates to` | ❌ 未支持 | 可归入 duplicate（一般关联），或单独加 `relates` |

## 三、方案对比

### 方案 A：保持简化（推荐当前）

维持现有 3 种，Jira 关系做如下映射：

```
is blocked by / blocks   → predecessor
sub-task                → subtask
duplicates / is duplicated by / clones / is cloned by / relates to  → duplicate
```

**优点**：前端、后端、数据库改动最小，用户界面简单清晰。
**缺点**：丢失克隆（clone）和一般关联（relates to）的细粒度语义。

### 方案 B：扩展为 5 种

在现有 3 种基础上新增 2 种：

| 新增关系 | 对应 Jira | 语义 |
|---------|-----------|------|
| **clone** | `clones` / `is cloned by` | 克隆（复制后独立） |
| **relates** | `relates to` | 一般关联 |

数据库 enum：`subtask` / `predecessor` / `duplicate` / `clone` / `relates`（5 种）。

前端画布：clone 用绿色虚线 + "克隆" label，relates 用灰色点线 + "关联" label。

### 方案 C：完全对齐 Jira（不推荐）

前端暴露全部 7 种 Jira 关系类型。

**缺点**：
1. `is blocked by` 和 `blocks` 本质是同一件事的两个方向，用户容易混淆。
2. `duplicates` 和 `is duplicated by` 同理。
3. Jira 允许管理员自定义 Issue Link 类型，未来可能超过 7 种。
4. 前端 UI 复杂，用户需要理解有向边的方向含义。

## 四、数据库 enum 设计建议

### 当前（3 种）
```sql
relation_type ENUM('subtask', 'predecessor', 'duplicate')
```

### 方案 B（5 种）
```sql
relation_type ENUM('subtask', 'predecessor', 'duplicate', 'clone', 'relates')
```

### 方向处理

- `subtask`：source = 父，target = 子（层级归属）
- `predecessor`：source = 前置，target = 主工单（前置 → 被依赖方）
- `duplicate` / `clone` / `relates`：双向，存两条记录或存方向但 UI 上合并显示

## 五、前端画布边样式建议

| 关系 | 线样式 | 颜色 | Label | 箭头 |
|------|--------|------|-------|------|
| subtask | 灰色实线折线 | #94a3b8 | 子任务 | ▶ |
| predecessor | 红色虚线 | #ef4444 | 前置 | ↓ |
| duplicate | 紫色虚线 | #a855f7 | 重复 | ↔ 双向 |
| clone（新增） | 绿色虚线 | #22c55e | 克隆 | ↔ 双向 |
| relates（新增） | 灰色点线 | #64748b | 关联 | ↔ 双向 |

## 六、同步 Jira 时的映射规则

```python
JIRA_LINK_TYPE_MAP = {
    # (inward_label, outward_label) → our relation_type
    ('is blocked by', 'blocks')           : 'predecessor',
    ('sub-task', 'parent')                : 'subtask',      # 注：sub-task 不走 Issue Link
    ('duplicates', 'is duplicated by')    : 'duplicate',
    ('clones', 'is cloned by')            : 'clone',        # 仅方案 B
    ('relates to', 'relates to')          : 'relates',      # 仅方案 B
}
```

## 七、结论

建议采用 **方案 A（保持 3 种）** 或 **方案 B（扩展 5 种）**。
如果业务上确实需要区分"克隆"和"重复"、或者需要"一般关联"这种宽松关系，选方案 B。
否则方案 A 已经覆盖了 90% 的工单关系场景。
