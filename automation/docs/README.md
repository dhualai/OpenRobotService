# docs/ - 测试文档

## 顶层文档

| 文档 | 内容 |
|------|------|
| `QUICKSTART.md` | 快速开始 |
| `README.md` | 本文（文档导航） |
| `automation_strategy.md` | 自动化测试方案总览（分层/策略/CI） |
| `API_TESTING.md` | API 测试说明 |
| `AI_TESTING.md` | AI 评估测试说明 |
| `AI_EXTERNAL_EVAL.md` | 外部评测说明 |
| `CONTRACT_TESTING.md` | OpenAPI 契约测试 |
| `UI_TESTING.md` | UI 测试说明 |
| `UI_REGRESSION.md` / `UI_REGRESSION_CI.md` | UI 回归与 CI |
| `REMOTE_TESTING.md` | 远程环境测试 |
| `ENVIRONMENT_SCHEDULING.md` | 环境调度 |
| `TICKET_LOOP.md` / `TICKET_GATE.md` / `TICKET_PIPELINE.md` | 工单自动化闭环、测试门禁与流水线 |
| `LOCAL_ENV_SETUP.md` | 本地环境搭建 |
| `prompt-template-generate-cases.md` | 用例生成提示词模板 |
| `design-openrobot-test-runtime.md` | 测试运行时设计（现行） |

## testing/ - 框架规范与场景

- `framework-design.md`、`directory-structure.md`、`naming-conventions.md`、`quick-reference.md`、`review-checklist.md`、`template-test-case.md`、`test-data.md`、`utilities.md`、`fixture-and-mock.md`、`index.md` 等
- `analysis/` — 各模块 7 要素业务分析
- `scenarios/` — 各模块 8 覆盖类型场景设计

## archive/ - 历史归档（勿在正式导航中引用）

`archive/` 收纳**已完成使命的一次性过程产物**，仅供追溯，不代表当前口径：

- `archive/worklog/` — 按任务编号的开发记录（`task-NN-*.md`）
- `archive/design-*.md` — 各阶段设计稿（多数头部标注"设计稿待确认"，已实施或已废弃）
- `archive/gap-analysis-*.md` — 某次测试缺口分析快照
- `archive/ci-ai-test-pipeline.md`、`archive/design-implementation.md` — 早期方案

> 新增的**现行**设计请放 `docs/` 顶层，不要放 `archive/`。

