# AI 测试用例清单

> 格式按 [template-test-case.md](template-test-case.md)

---

## LLMClient

### AI-TC-001 — test_chat_returns_string

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 返回类型校验

**测试点：** 验证 LLMClient.chat() 返回字符串类型

**前置条件：** MockLLMClient 已初始化

**测试步骤：**
1. 调用 client.chat(messages) → 返回 str 类型

**结果：** PASS

---

## LLMClient

### AI-TC-002 — test_chat_error_keyword

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 error 关键词触发故障回复

**测试点：** 验证输入含 error 关键词时返回故障消息

**前置条件：** MockLLMClient 已初始化

**测试步骤：**
1. 发送含 error 关键词的消息 → 返回故障回复

**结果：** PASS

---

## LLMClient

### AI-TC-003 — test_chat_reset_keyword

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 reset 关键词触发重置回复

**测试点：** 验证输入含 reset 关键词时返回重置消息

**前置条件：** MockLLMClient 已初始化

**测试步骤：**
1. 发送含 reset 关键词的消息 → 返回重置回复

**结果：** PASS

---

## LLMClient

### AI-TC-004 — test_chat_default_response

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 默认 mock 回复

**测试点：** 验证默认情况下返回 mock 回复

**前置条件：** MockLLMClient 已初始化

**测试步骤：**
1. 发送正常消息 → 返回默认 mock 回复

**结果：** PASS

---

## LLMClient

### AI-TC-005 — test_chat_stream

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 流式调用

**测试点：** 验证流式 chat 接口正常工作

**前置条件：** MockLLMClient 已初始化

**测试步骤：**
1. 调用 client.chat_stream(messages) → 返回 AsyncGenerator

**结果：** PASS

---

## LLMClient

### AI-TC-006 — test_empty_messages

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 空消息处理

**测试点：** 验证空消息输入时的处理

**前置条件：** MockLLMClient 已初始化

**测试步骤：**
1. 发送空消息列表 → 返回默认回复或错误

**结果：** PASS

---

## Evaluators

### AI-TC-007 — test_all_keywords_found

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 全部关键词匹配通过

**测试点：** 验证所有预期关键词都出现在回答中

**前置条件：** AccuracyEvaluator 已初始化

**测试步骤：**
1. 给定 answer 含所有 expected_keywords → passed=True

**结果：** PASS

---

## Evaluators

### AI-TC-008 — test_partial_keywords_found

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 部分关键词匹配通过

**测试点：** 验证部分关键词匹配时通过

**前置条件：** AccuracyEvaluator 已初始化

**测试步骤：**
1. 给定 answer 含部分 expected_keywords → passed=True

**结果：** PASS

---

## Evaluators

### AI-TC-009 — test_no_keywords_found

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 无关键词匹配失败

**测试点：** 验证无关键词匹配时失败

**前置条件：** AccuracyEvaluator 已初始化

**测试步骤：**
1. 给定 answer 不含任何 keyword → passed=False

**结果：** PASS

---

## Evaluators

### AI-TC-010 — test_no_expected_keywords

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 无预期关键词自动通过

**测试点：** 验证无预期关键词时自动通过

**前置条件：** AccuracyEvaluator 已初始化

**测试步骤：**
1. expected_keywords 为空 → passed=True

**结果：** PASS

---

## Evaluators

### AI-TC-011 — test_evaluate_batch

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 批量评估

**测试点：** 验证批量评估多个回答的功能

**前置条件：** AccuracyEvaluator 已初始化

**测试步骤：**
1. 传入多个 (question, answer) 对 → 返回批量评估结果

**结果：** PASS

---

## Evaluators

### AI-TC-012 — test_clean_response

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 无不确定性表达通过

**测试点：** 验证回答中无 hedging 表达时通过

**前置条件：** HallucinationEvaluator 已初始化

**测试步骤：**
1. 给定 clean answer → passed=True

**结果：** PASS

---

## Evaluators

### AI-TC-013 — test_hedging_response

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 大量 hedging 失败

**测试点：** 验证回答中含大量 hedging 时失败

**前置条件：** HallucinationEvaluator 已初始化

**测试步骤：**
1. 给定含大量 hedging 表达的 answer → passed=False

**结果：** PASS

---

## Evaluators

### AI-TC-014 — test_mixed_response

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 混合场景

**测试点：** 验证混合正常和 hedging 的回答

**前置条件：** HallucinationEvaluator 已初始化

**测试步骤：**
1. 给定混合场景 answer → 视 hedging 比例判定

**结果：** PASS

---

## Evaluators

### AI-TC-015 — test_summary_property

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 evaluator summary 属性

**测试点：** 验证 evaluator 的 summary 属性

**前置条件：** Evaluator 实例已创建

**测试步骤：**
1. 调用 evaluator.summary → 返回评估摘要

**结果：** PASS

---

## Evaluators

### AI-TC-016 — test_to_dict

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 EvaluationResult 序列化

**测试点：** 验证 EvaluationResult 的 to_dict 方法

**前置条件：** EvaluationResult 实例已创建

**测试步骤：**
1. 调用 result.to_dict() → 返回序列化 dict

**结果：** PASS

---

## RAG Scenarios

### AI-TC-017 — test_scenarios_loaded

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 YAML 场景加载

**测试点：** 验证 RAG 测试场景配置文件成功加载

**前置条件：** 场景 YAML 文件存在

**测试步骤：**
1. 加载场景文件 → 返回场景列表

**结果：** PASS

---

## RAG Scenarios

### AI-TC-018 — test_rag_001_basic_error_code

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 RAG 错误码场景

**测试点：** 验证错误码 RAG 场景

**前置条件：** 场景#001 已定义

**测试步骤：**
1. 加载场景#001 → 按场景执行验证

**结果：** PASS

---

## RAG Scenarios

### AI-TC-019 — test_rag_002_reset_procedure

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 RAG 重启流程场景

**测试点：** 验证重启流程 RAG 场景

**前置条件：** 场景#002 已定义

**测试步骤：**
1. 加载场景#002 → 按场景执行验证

**结果：** PASS

---

## RAG Scenarios

### AI-TC-020 — test_rag_003_maintenance_schedule

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 RAG 维护计划场景

**测试点：** 验证维护计划 RAG 场景

**前置条件：** 场景#003 已定义

**测试步骤：**
1. 加载场景#003 → 按场景执行验证

**结果：** PASS

---

## RAG Scenarios

### AI-TC-021 — test_rag_005_multi_part_diagnostic

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 RAG 多步诊断场景

**测试点：** 验证多步诊断 RAG 场景

**前置条件：** 场景#005 已定义

**测试步骤：**
1. 加载场景#005 → 按场景执行验证

**结果：** PASS

---
