# ai/ — AI 自动化测试模块

## 职责
- 对三个 AI Agent（提单 Agent、任务 Agent、管理 Agent）进行质量评估。
- AI 输出具有非确定性，本模块采用"评分阈值"代替传统断言。

## 结构
| 目录/文件 | 说明 |
|-----------|------|
| conftest.py | LLM 客户端注入、场景注册 Fixture |
| evaluators/ | 评估器：relevance/accuracy/hallucination/safety |
| scenarios/ | 声明式 YAML 场景（由 PM 或业务方维护） |
| 	ests/ | AI 测试用例，读取 scenarios → 调用 Agent → evaluator 打分 |
| utils/ | 评测用 LLM 客户端 |

## 评估策略
- 双评估器：主评估器（DeepSeek API）+ 辅评估器（Embedding 余弦相似度）
- 评估器返回 0-1 分数，低于阈值标记失败
- 二者分歧超过阈值时标记为需人工审核
