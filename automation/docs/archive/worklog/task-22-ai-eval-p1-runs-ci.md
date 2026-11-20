# task-22-ai-eval-p1-runs-ci.md

## 本次目标

P1 落地 ai-evaluation-skill 方法论两项：**评测运行版本化**(run-id + run.json + results.csv 长表 + 版本对比 CLI) + **持续评测触发**(`.github/workflows/ai-eval.yml`)。设计稿：`automation/docs/design-ai-eval-p1-runs-ci.md`。

## 阅读内容

- `automation/AGENTS.md`、`automation/docs/AI_TESTING.md`、`automation/docs/design-ai-eval-p1-runs-ci.md`
- `automation/src/ai_metrics/run_recorder.py`(本次新增，v0)、`automation/tests/ai/runner.py`(4 个 run 函数)、`tests/ai/conftest.py`(fixture 与钩子)
- `.github/workflows/ai-test.yml`(CI 惯例：services/setup-python/artifact)

## 修改文件

| 文件 | 说明 |
|------|------|
| `automation/src/ai_metrics/run_recorder.py` | 新增：运行记录器（RunRecorder/单例/dump_if_needed） |
| `automation/src/ai_metrics/tests/test_run_recorder.py` | 新增：10 条自测 |
| `automation/src/ai_metrics/tests/test_cli_compare_ai_runs.py` | 新增：4 条对比逻辑自测（importlib 加载脚本） |
| `automation/src/ai_metrics/__init__.py` | 导出 run_recorder |
| `automation/tests/ai/runner.py` | 4 个 run_*_case 增加 suite 参数并记录；loaded_suite_counts() |
| `automation/tests/ai/conftest.py` | pytest_sessionfinish 钩子：dump 运行记录 + golden fingerprint |
| `automation/tests/ai/test_diagnosis.py` 等 4 个 | 调用处补 suite 参数 |
| `automation/scripts/cli-compare-ai-runs.py` | 新增：版本对比（迁移表/回归清单/--fail-on-regression） |
| `.github/workflows/ai-eval.yml` | 新增：持续评测 workflow（定时/手动/路径触发，变量门控） |
| `automation/docs/AI_TESTING.md` | 增补第 12 章：运行版本化与持续评测 |
| `automation/docs/design-ai-eval-p1-runs-ci.md` | 新增：P1 设计稿 |

未修改：`ai/` `backend/` `frontend/` 任何业务代码；未改动既有 workflow。

## 测试结果

```
65 passed (src/ai_metrics/tests/，Fast Lane：51 既有 + 10 recorder + 4 compare)
pytest tests/ai/ 实跑验证：AI_EVAL_RUN_ID=demo-run-1 → output/ai-eval-runs/demo-run-1/ 生成 run.json+results.csv，
  counts=14 全部 skipped（无服务优雅跳过），golden_fingerprint={assigner:6, data_analysis:4, diagnosis:10, rag_retrieval:8}
对比 CLI 实测：--latest-two / --base --new / --fail-on-regression 路径均验证（测试数据已清理）
```

## 风险

- CI workflow 默认 `AI_EVAL_ENABLED` 未开：需真实 AI 服务 + DeepSeek key + 知识库入库才能真跑，基础设施就绪后再开变量
- `pytest_sessionfinish` 钩子：trylast + 无记录跳过 + 异常不改变退出码，已实测无副作用
- 版本对比在 golden 变更后失真：fingerprint 警告机制已实现

## 下一步建议

1. 启动 AI 服务后真跑 `tests/ai/` 校准 L1 阈值与 veto 判定（当前基于契约）
2. P2：人工复核门禁轻量版（否决候选清单 human_review.csv）
3. CI 基础设施就绪后开启 `AI_EVAL_ENABLED` 并补 Allure 报告发布
