## ai/ — AI 评测模块

本目录包含对三个 AI Agent 的评测用例。
- evaluators/ 存放评估器（relevance / accuracy / hallucination / safety）
- scenarios/ 存放测试场景 YAML
- 评估策略：主评估器 DeepSeek + 辅评估器 Embedding

预期文件：test_agent_chat.py、test_rag.py、test_report.py
