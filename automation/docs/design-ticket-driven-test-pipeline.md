# 工单驱动测试用例生成与执行设计（候选）

> 状态：设计稿，待人工确认
> 日期：2026-09-15
> 范围：生产工单 -> AI 生成功能用例 -> 人工 review -> test 分支执行 -> 报告与门禁

## 1. 目标流程

把自动化测试从“代码提交触发”调整为“工单驱动生成 + test 分支合并触发”：

```text
扫描生产环境 001 / 摇人吧服务号
        |
        | 新建状态、feature/bug 类工单
        v
AI 生成候选功能测试用例
        |
        v
人工 review / 修改 / 确认
        |
        v
归档为正式功能用例 + 回归用例库
        |
        | GitHub 检测到代码合入 test 分支
        v
立即执行：
  1. 本次工单对应的功能用例
  2. 已有回归用例
        |
        v
生成 Allure 报告 + 通过率门禁
```

整体分两阶段：

- **用例生成阶段**：生产工单是输入，AI 负责候选用例生成，人工负责确认。
- **用例执行阶段**：GitHub `push test` 是触发点，执行功能用例和回归用例。

### 1.1 已确认规则

1. 只处理生产 `project_id='Leo_test'` 的 `feature`、`bug` 工单，并且必须带专用标签 `auto_case`。
2. AI 生成输入只取工单标题和描述，不取附件、评论和聊天记录。
3. 人工 review 统一走 GitHub PR。
4. commit/PR 必须关联工单号，用于选择本次要执行的功能用例。
5. 85% 通过率门禁第一阶段只报告、不阻断 `test -> dev`，先跑通流程。

## 2. 工单筛选规则

第一版只处理：

- 生产扫描项目：`project_id='Leo_test'`（项目名称“摇人吧服务号”）
- 状态：`new`
- 类型：`feature` / `bug`
- 专用标签：`auto_case`
- 非自动化机器人创建

生产库 `helpdesk_724` 实际核对结果：

| 项目表字段 | 实际值 |
|---|---|
| `project.id=Leo_test` | `project.name=摇人吧服务号` |
| `project.id=001` | `project.name=test` |

已确认扫描条件使用：

```text
project_id = 'Leo_test'
```

同时保留 `project_name='摇人吧服务号'` 作为展示和报告名称，不把它作为唯一过滤条件。

标签核对结果：

- 当前 feature/bug 工单标签只有 `ai_generated`。
- 没有现成的“待自动化生成”标签。
- 第一版建议新增业务标签 `auto_case`，只处理带该标签的工单。

版本/模块字段核对结果：

- 有 `title`、`description`、`task_type`、`status`、`tags`、`metadata_info`。
- 没有独立的 `module`、`version`、`commit` 字段。
- 第一版无法从工单直接获得版本或 commit。

类型映射：

| 业务说法 | 系统类型 |
|---|---|
| 需求类 | `feature` |
| bug类 | `bug` |

如果一开始放开所有 feature/bug 工单，AI 调用量和 review 压力会很大。建议第一版增加人工标记或白名单。

## 3. 用例生成阶段

### 3.1 输入

已确认只读取：

- `id`
- `title`
- `description`

目的：

- 减少生产数据暴露面，避免读取客户隐私。
- 降低 LLM token 和生成成本。
- 避免评论/附件里的噪声干扰用例生成。
- 让候选用例的输入边界清晰，便于 review。

附件、评论、聊天记录不在第一版自动拉取，避免合规风险。

### 3.2 生成方式

复用现有 `automation/ci_ai_gen` 流水线，新增“工单驱动模式”：

```text
工单 -> 功能点分析 -> 候选功能用例 -> 候选 pytest 脚本 -> gate
```

产物建议：

```text
automation/references/generated-cases/tickets/{ticket_id}/
├── analysis.md
├── cases.json
├── cases.xlsx
├── test_gen.py
└── manifest.json
```

候选用例必须携带：

- 来源工单 ID
- 工单类型
- 业务模块
- 功能点
- 预期结果
- 是否属于核心链路
- AI 生成版本

### 3.3 人工 review

AI 产物不能直接入库，必须经过人工 review。

已确认 review 方式：

- AI 产物生成到候选目录或候选分支。
- AI 创建 GitHub PR。
- 人工在 PR 中查看 diff、修改和确认。

目的：

- AI 产物不能直接进入正式用例库。
- 保留 review 记录、修改历史和审批痕迹。
- 复用 GitHub 的 diff、评论和 CI 能力，不另建审核平台。
- 便于回滚、追责和后续审计。

Review 结果：

- 通过：归档为正式功能用例。
- 修改后通过：以人工修改版本为准。
- 拒绝：保留候选和拒绝原因，不进入执行集。

## 4. 归档规则

建议正式功能用例使用代码驱动方式归档，符合当前自动化平台规范：

```text
automation/tests/generated/{module}/test_ticket_{ticket_id}.py
```

同时维护工单到用例的映射：

```text
automation/testdata/ticket-case-map.yaml
```

示例：

```yaml
 "001-12345":
   ticket_type: feature
   module: call
   suite: call_to_ticket_close
   cases:
     - automation/tests/generated/call/test_ticket_001_12345.py
   regression: true
   priority: P0
```

## 5. 执行阶段

### 5.1 触发点

只允许：

- `push test`
- `workflow_dispatch`

不配置 `pull_request` 执行真实环境完整链路。

### 5.2 本次功能用例选择

优先级：

1. 从 commit message / PR 标题描述中解析工单号，例如 `001-{ticket_id}`。
2. 从 `ticket-case-map.yaml` 查找对应功能用例。
3. 找不到精确映射时，执行最近一次归档且尚未运行的工单用例。
4. 默认回退：只跑 smoke，不跑全量真实链路。

当前生产工单没有 `source_commit` / `test_version` 字段，第一版版本解析默认使用：

```text
GitHub test 分支当前 commit
```

报告必须标记“版本未由工单固定”，避免把 test 分支结果误认为精确验证了生产工单版本。

目的：

- 代码合入 `test` 后，系统知道本次要跑哪个工单生成的功能用例。
- 避免每次都把所有功能用例跑一遍。
- 保留“本次功能用例 + 回归用例”的可追溯关系。

### 5.3 回归用例

每次 `push test` 固定执行：

- 已审阅通过的功能用例
- 已有回归用例
- 核心业务链路场景用例

建议第一版把“本次功能用例”和“回归用例”分成两个 Allure 顶层分类，便于报告阅读。

## 6. 报告与通过率门禁

### 6.1 通过率计算

建议明确分母：

```text
pass_rate = passed / (passed + failed + broken)
```

- `skipped` 不计入分母，但报告中单独展示。
- 全部 skipped 或 0 条执行用例，视为未通过。
- 结束时间、环境版本、commit、工单号必须写入报告。

### 6.2 85% 门禁（第一阶段只报告）

建议：

- 第一阶段仍计算并展示总体通过率，但不阻断 `test -> dev`。
- 总体通过率 >= 85% 标记为“通过率达标”。
- 总体通过率 < 85% 标记为“通过率未达标”，只告警，不阻断流程。
- P0 / 核心业务链路的通过情况单独展示，便于后续升级为阻断门禁。
- 出现 `broken` 时单独标记，不纳入 85% 的容错。

后续流程稳定后，再决定是否把 85% 和 P0 全通过升级为阻塞门禁。

### 6.3 报告结构

Allure 建议分为：

- `功能用例`：本次工单生成并归档的用例
- `回归用例`：原有回归用例
- `核心链路`：摇人问答到工单关闭等关键场景

每份报告必须回写：

- 工单号
- GitHub commit / test 分支版本
- 测试环境
- 通过率
- 失败用例清单
- 是否满足门禁

## 7. 防止递归和误触发

- 生成用例时不得自动修改生产工单状态。
- 执行阶段创建的测试工单必须带 `AUTO-` 标识。
- 自动化创建的工单不能再次参与工单生成扫描。
- 用例归档提交不能被误认为产品代码合并。
- 工作流建议使用 `paths-ignore` 排除纯测试文档和候选目录。

## 8. 失败处理

- AI 生成失败：记失败原因，不阻塞产品流程。
- 人工 review 超时：进入待办列表，不自动执行。
- `test` 分支执行失败：报告失败并告警。
- 通过率 <85%：标记门禁失败。
- 核心链路失败：无论总体通过率多少都标记失败。

## 9. 实施阶段

### 阶段一：扫描和生成

- 只读扫描 `001` 的 feature/bug 新建工单。
- 调 `ci_ai_gen` 生成候选用例。
- 人工 review 后归档。

### 阶段二：执行和报告

- `push test` 触发工作流。
- 根据工单映射选择功能用例。
- 同时执行回归用例。
- 生成 Allure 报告和通过率门禁结果。

### 阶段三：自动化闭环

- 报告回写 GitHub PR/commit。
- 可选回写生产工单评论。
- 加入 review 队列和失败重跑入口。

## 10. 待确认问题

1. 工单号关联格式最终采用 `001-{ticket_id}` 还是其他格式？
2. P0 / 核心业务链路是否需要在第一阶段单独 100% 通过？
3. 生成用例数量大时如何限流和分批 review？
4. 第一阶段报告是否需要回写生产工单，还是只贴 GitHub PR？

## 11. 推荐第一版

```text
project_id=Leo_test
+ feature/bug + auto_case
+ AI 生成候选用例
+ PR/人工 review
+ 归档到 automation/tests/generated
+ push test 触发
+ 本次功能用例 + 回归用例
+ Allure 报告
+ 总体通过率 >=85% 只报告、不阻断
```
