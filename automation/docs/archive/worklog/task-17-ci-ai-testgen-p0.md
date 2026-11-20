# task-17-ci-ai-testgen-p0.md

## 本次目标

P0：落地"代码推送 → 自动读取代码 → AI 需求分析 → 生成用例 → 生成脚本 → 执行 → Allure 报告"流水线的骨架，设计文档见 `automation/docs/ci-ai-test-pipeline.md`。

## 阅读内容

- `automation/AGENTS.md`、现有 `.github/workflows/test.yml`（services/report 复用）
- `backend/app/__init__.py`（FastAPI app 入口，openapi 兜底路径）
- 并行重构后的平台结构（src/、config/paths.py、testdata/fixtures/）

## 修改文件

| 文件 | 说明 |
|------|------|
| `automation/docs/ci-ai-test-pipeline.md` | 设计文档 + 决策记录 + P0 实现记录 |
| `automation/ci_ai_gen/__init__.py` | 模块 |
| `automation/ci_ai_gen/prompts/{analyzer,case_gen,script_gen,gate}.md` | 四角色提示词 |
| `automation/ci_ai_gen/extract_api.py` | 接口提取（--url 或直读 app.openapi()） |
| `automation/ci_ai_gen/gates.py` | 结构门禁纯函数（分析标题/用例 JSON/脚本路径模板匹配） |
| `automation/ci_ai_gen/run_pipeline.py` | 编排（LLM 可注入，gate 2 轮修复，降级策略） |
| `automation/ci_ai_gen/tests/` | 15 条测试（fake LLM） |
| `automation/pyproject.toml` | testpaths 增加 ci_ai_gen |
| `.github/workflows/ai-test.yml` | push/PR 触发流水线（复用 services + Allure artifact） |
| `.gitignore` | 增加 /test-gen/ |

未修改业务代码（backend/ 只读）。

## 测试结果

```
15 passed (automation/ci_ai_gen/tests/)
252 passed, 28 skipped (全量回归，含并行重构后的 src/config/tests)
```

## Allure 报告

本地验证生成成功（`automation/output/allure-report`）。

## 风险与说明

- 全流程需 GitHub Actions 环境实测（本机无法模拟 push 触发）
- LLM 生成质量依赖提示词；gate 硬校验兜底（编译/收集/接口存在性）
- 并行重构适配：ai_metrics 已迁移至 src/ai_metrics/，ci_ai_gen 引用新路径
- 每次 push 全量重新生成（run_id 隔离），不增量复用

## 下一步建议

1. 推送 backend/ 变更实测 workflow，校准提示词与阈值
2. P1：用例/脚本人工反馈回流（PR review 评论 → 提示词迭代）；token 用量看板
3. P2：平台化模式 B（多业务仓统一收件）
