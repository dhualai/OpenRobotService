# task-16-ai-eval-framework.md

## 本次目标

建设 P2「AI 质量评估」模块：三层评估金字塔（L1 确定性断言 / L2 faithfulness+recall / L3 LLM-judge），golden 数据驱动，落地 `automation/`。

## 阅读内容

- `automation/AGENTS.md`、`.agents/automation-test-agent.md`
- `automation/docs/AI_TESTING.md`（空占位，本次填充）、`docs/README.md`
- `ai/agents/AiDiagnosisPlatform/pipeline.py`（DiagnosisRequest / skip_retrieval / run 契约）
- `ai/agents/AiDiagnosisPlatform/assigner/`（assign_ticket 签名、工程师画像示例）
- `ai/agents/AiDataAnalysisPlatform/`（router / analyzer / schemas 契约）
- `ai/core/retrieval.py`（RetrievalService 5 路接口）
- `automation/tests/common/test_runner.py`（数据驱动模式参照）、`automation/conftest.py`

## 修改文件

| 文件 | 说明 |
|------|------|
| `automation/docs/AI_TESTING.md` | 设计文档（背景/架构/golden 格式/指标/步骤/风险/决策/实现偏差） |
| `automation/src/ai_metrics/__init__.py` | 指标统一导出 |
| `automation/src/ai_metrics/schema_validity.py` | L1：点路径 schema 校验（类型/枚举） |
| `automation/src/ai_metrics/keyword_hit.py` | L1：关键词命中率 |
| `automation/src/ai_metrics/retrieval_recall.py` | L2：collection 级 recall 汇总 |
| `automation/src/ai_metrics/faithfulness.py` | L2：防幻觉忠实度（judge 注入） |
| `automation/src/ai_metrics/llm_judge.py` | L3：LLMJudgeClient（openai SDK）+ rubric 打分 |
| `automation/src/ai_metrics/tests/test_schema_validity.py` | 指标自测（21 条） |
| `automation/src/ai_metrics/tests/test_retrieval_recall.py` | 指标自测（10 条） |
| `automation/src/ai_metrics/tests/test_llm_judge.py` | 指标自测（11 条，fake judge） |
| `automation/testdata/ai/diagnosis.json` | 10 条诊断 golden（含 L2 参考文档 + L3 rubric） |
| `automation/testdata/ai/assigner.json` | 6 条派单 golden |
| `automation/testdata/ai/rag_retrieval.json` | 8 条检索 recall golden |
| `automation/testdata/ai/data_analysis.json` | 4 条数据分析 golden |
| `automation/tests/ai/__init__.py` | 包标记 |
| `automation/tests/ai/conftest.py` | ai_client fixture（session 级，健康检查后 skip） |
| `automation/tests/ai/runner.py` | load_ai_cases + run_ai_case / run_rag_case / run_assigner_case / run_analysis_case |
| `automation/tests/ai/test_diagnosis.py` | 诊断评估（10 参数化） |
| `automation/tests/ai/test_rag.py` | 检索评估（8 参数化） |
| `automation/tests/ai/test_assigner.py` | 派单评估（6 参数化） |
| `automation/tests/ai/test_data_analysis.py` | 数据分析评估（4 参数化） |
| `automation/pyproject.toml` | 新增 marker `judge` |

未修改：`ai/`、`backend/`、`frontend/` 任何业务代码（仅只读导入契约）。

## 测试结果

```
42 passed (infrastructure/ai_metrics/tests/，Fast Lane)
28 skipped (tests/ai/：服务 8401 未运行 / AI 运行时依赖未安装 → 按设计优雅跳过)
246 passed, 28 skipped (全量回归 infrastructure/ + tests/)
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`（含 tests/ai 全部用例，skipped 有明确原因描述）。

## 风险与限制

- 本环境无 AI 服务 + AI 运行时依赖（tenacity 等），诊断/数据分析用例在服务启动后即可真跑；RAG/派单需在 AI 环境执行
- L2 faithfulness 采用 golden 内嵌参考文档（HTTP 无检索结果暴露），后续如需真实检索文档评估需在 `ai/` 增加只读 debug 端点（须产品经理确认）
- `/api/ai/ticketReferee` 文档存在但路由未实现，派单评估走直接导入 `assign_ticket`
- judge 调用有 token 成本：默认 L1+L2 快跑，L3 由 `-m judge` 或全量触发

## 下一步建议

1. 启动 AI 服务后真跑 `tests/ai/` 校准阈值（当前 L1 断言基于契约，可能需微调）
2. 下一期补任务 Agent 数据集（依赖 backend 任务上下文）
3. 将 AI 评估加入 fast-lane .bat 脚本（当前仅本地手动）
