# Task 39：工单驱动测试用例生成与执行设计

> 日期：2026-09-15
> 状态：设计稿，待确认

## 目标

根据新的触发逻辑调整设计：

```text
生产“摇人吧服务号”新建 feature/bug 工单
-> AI 生成候选功能用例
-> 人工 review
-> 归档功能用例和回归用例
-> GitHub push test
-> 执行本次功能用例 + 回归用例
-> Allure 报告
-> 总体通过率 >=85% 且 P0 100%
```

## 设计要点

- 生产触发项目固定为 `project_id='Leo_test'`（项目名称“摇人吧服务号”）。
- 第一版只处理 `feature` 和 `bug` 类型。
- 建议增加人工标记或白名单，避免所有工单都触发 AI 生成。
- 复用 `automation/ci_ai_gen`，新增工单驱动模式。
- AI 产物不直接入库，必须人工 review。
- 归档为 `automation/tests/generated/{module}/test_ticket_{id}.py`。
- `push test` 执行本次功能用例和回归用例。
- 通过率计算：`passed / (passed + failed + broken)`。
- 建议 P0/核心链路用例必须 100% 通过。

## 已确认

- 只处理生产 `project_id='Leo_test'`、带 `auto_case` 标签的 `feature` / `bug` 工单。
- AI 输入只取标题和描述。
- review 走 GitHub PR。
- commit/PR 必须关联工单号，用于选择本次功能用例。
- 85% 通过率第一阶段只报告，不阻断 `test -> dev`。

## 生产库只读核对

- 生产库：`helpdesk_724`
- `project.id=Leo_test` 对应 `project.name=摇人吧服务号`。
- `project.id=001` 当前对应 `project.name=test`，不能作为“摇人吧服务号”的扫描条件。
- feature/bug 工单当前标签只有 `ai_generated`，没有“待自动化生成”标签。
- 任务表没有独立的 `module`、`version`、`commit` 字段。
- 当前生产新建 feature/bug 工单：feature 3 条、bug 1 条（按 project_name 统计）。

## 建议修正

1. 专用标签使用 `auto_case`。
2. 生产扫描使用 `project_id='Leo_test'`。
3. 版本第一版使用 GitHub `test` 分支当前 commit，并在报告标记“版本未由工单固定”。

## 产出

- `automation/docs/design-ticket-driven-test-pipeline.md`

## 待确认

1. 工单号关联格式。
2. P0 / 核心链路是否单独要求 100%。
3. 生成用例的限流与归档方式。
4. 第一阶段报告回写位置。

## 下一步

1. 人工评审新流程设计。
2. 确认触发、筛选、review、归档和门禁规则。
3. 确认后再进入实现。
