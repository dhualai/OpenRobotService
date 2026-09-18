# OpenRobot 自动化测试运行规则

本规则适用于 AI 助手、MCP 工具、pytest 场景和真实环境测试。

## 1. 先探索，再写测试

- 编写或修改测试前，先通过真实 API、Playwright 或已有 pytest 场景确认实际行为。
- 不凭猜测编写接口路径、字段名、状态码、页面跳转或等待时间。
- 探索完成后，把验证过的交互固化为确定性 pytest 代码。

## 2. Fast / Pro 路由

- `fast`：Mock API、业务链路、框架单测。必须离线、快速、稳定，适合每次 PR。
- `pro`：真实后端、MySQL/Redis/Qdrant、Playwright Smoke、AI 评测。适合 test 分支、手工触发和夜间任务。
- `nightly`：全量契约测试、完整 E2E、全量 AI 评测和稳定性巡检。
- 不要用 AI 自由执行替代确定性回归用例。

## 3. 断言与等待

- 最终测试代码必须有显式等待、超时和确定性断言。
- 状态码、响应字段、数据库记录、缓存和检索结果都要验证。
- AI 输出使用 Schema、关键词、Recall、Faithfulness、Rubric 等指标，不做固定文本相等断言。
- skip 必须表达“未验收”，不能伪装成 passed。

## 4. UI 定位策略

优先级从高到低：

1. `data-testid`
2. role + accessible name
3. label / placeholder
4. 稳定文本
5. CSS
6. 坐标兜底

坐标只能作为最后兜底，并必须注明适用分辨率和页面版本。

## 5. Trace 与诊断

- 每次运行必须生成 `run_id` 和 `trace_id`。
- 步骤中记录动作、请求、响应、断言、耗时、截图和错误。
- 工具报错、环境不可达或真实链路失败时，先运行 `diagnose_environment`。
- 不通过盲目重跑掩盖环境或数据问题。

## 6. 数据与安全

- 生产工单扫描必须只读，只读取完成用例生成所需的最小字段。
- SSH key、数据库密码、LLM key 不进入聊天、日志或测试产物。
- 真实环境测试使用唯一前缀、幂等键和 teardown 清理。
- AI 生成的候选用例必须通过 GitHub PR 人工 Review 后才能进入正式套件。

## 7. 工单驱动流程

- 扫描条件：`project_id=Leo_test`、`status=new`、`task_type in (feature, bug)`、`tags` 包含 `auto_case`。
- 生成产物进入候选目录，标记 `pending_review`。
- Review 通过后归档为正式功能用例，并关联来源工单。
- 代码合并到 `test` 分支后，执行本次工单用例和已有回归用例。

## 8. 禁止事项

- 禁止让 AI 在 CI 中自由决定最终点击路径或断言。
- 禁止把真实凭证写入仓库。
- 禁止在没有清理和幂等保护时反复写真实环境数据。
- 禁止将未执行的 AI 评测、真实链路或 UI 用例报告为通过。