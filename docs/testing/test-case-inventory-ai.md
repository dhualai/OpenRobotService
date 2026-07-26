# AI 测试用例清单

> 3 个模块，21 个测试用例。详情见 [ai-test-plan.md](ai-test-plan.md)。

## LLMClient（7 用例）

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 1 | test_chat_returns_string | 返回类型校验 |
| 2 | test_chat_error_keyword | error 关键词触发故障回复 |
| 3 | test_chat_reset_keyword | reset 关键词触发重置回复 |
| 4 | test_chat_default_response | 默认 mock 回复 |
| 5 | test_chat_stream | 流式调用 |
| 6 | test_empty_messages | 空消息处理 |

## Evaluators（14 用例）

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 7 | test_all_keywords_found | 全部关键词匹配→通过 |
| 8 | test_partial_keywords_found | 部分关键词匹配→通过 |
| 9 | test_no_keywords_found | 无关键词匹配→失败 |
| 10 | test_no_expected_keywords | 无预期关键词→自动通过 |
| 11 | test_evaluate_batch | 批量评估 |
| 12 | test_clean_response | 无不确定性表达→通过 |
| 13 | test_hedging_response | 大量 hedging→失败 |
| 14 | test_mixed_response | 混合场景 |
| 15 | test_summary_property | evaluator summary 属性 |
| 16 | test_to_dict | EvaluationResult 序列化 |
| 17 | test_scenarios_loaded | YAML 场景加载 |
| 18 | test_rag_001_basic_error_code | RAG 错误码场景 |
| 19 | test_rag_002_reset_procedure | RAG 重启流程场景 |
| 20 | test_rag_003_maintenance_schedule | RAG 维护计划场景 |
| 21 | test_rag_005_multi_part_diagnostic | RAG 多步诊断场景 |

**合计：21 用例 ✅**
