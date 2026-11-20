# AI 模块测试计划

## 概述

AI 测试覆盖 LLM 调用、RAG 检索、Agent 行为评估。
当前模块目录 `automation/ai/` 已规划骨架结构，尚未实现。

## 规划架构

```
ai/
├── conftest.py         → AI 测试 fixture
├── evaluators/         → LLM 输出评估器
│   ├── accuracy.py     → 答案正确性评估
│   ├── hallucination.py → 幻觉检测
│   ├── relevance.py    → 相关性评估
│   └── safety.py       → 安全性检查
├── scenarios/          → 测试场景配置（YAML）
│   ├── rag_scenarios.yaml    → RAG 检索场景
│   ├── task_scenarios.yaml   → 任务 Agent 场景
│   └── wechat_scenarios.yaml → 微信对话场景
├── utils/
│   └── llm_client.py   → LLM 调用封装
└── tests/
    ├── test_rag.py         → RAG 检索测试
    ├── test_agent_chat.py  → Agent 对话测试
    └── test_report.py      → 报告生成测试
```

## 待实现功能

| 功能 | 优先级 | 前置依赖 |
|------|--------|----------|
| evaluator 框架定义 | P1 | — |
| RAG 场景数据 | P1 | Qdrant 环境 |
| Mock LLM 服务 | P1 | `automation/mocks/` |
| 幻觉评估器 | P2 | evaluator 框架 |
| Agent 对话测试 | P2 | Mock LLM |
| 批评估报告 | P2 | evaluator 框架 |

## 当前状态

**状态**：⬜ 骨架就绪，待实现
**测试数**：0
**依赖**：LLM API 或 Mock LLM、Qdrant 向量库
**建议开始条件**：DB 模块 + CI 就绪
