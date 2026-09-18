# 外部 AI 评测：DeepEval / Ragas

现有 L1/L2/L3 继续作为默认评测体系。DeepEval 和 Ragas 作为可选增强层。

## 安装

```powershell
cd D:\WorkCode\OpenRobotService
pip install -e "automation/[external-eval]"
```

## DeepEval

适配器：`automation/src/ai_metrics/external_eval.py`

适合：

- answer relevancy
- faithfulness
- hallucination
- agent trajectory / tool use

调用：

```python
from automation.src.ai_metrics import run_deepeval

result = run_deepeval({
    "input": "机器人无法启动怎么办？",
    "actual_output": "请先检查急停、电源和故障码...",
    "retrieval_context": ["操作手册内容..."],
    "expected_output": "参考回答...",
    "metrics": ["answer_relevancy", "faithfulness"],
    "threshold": 0.7,
})
```

## Ragas

适合：

- faithfulness
- answer relevancy
- context precision
- context recall

调用：

```python
from automation.src.ai_metrics import run_ragas

result = run_ragas([
    {
        "question": "E1001 怎么处理？",
        "answer": "先检查...",
        "contexts": ["知识库片段..."],
        "ground_truth": "标准处理步骤...",
    }
])
```

## 运行策略

- 未安装可选依赖时返回 `available=false, skipped=true`，不阻塞主框架。
- PR 默认不跑外部评测。
- 夜间或 Prompt/模型/知识库变更时运行真实 DeepEval/Ragas。
- judge 失败、依赖缺失、全部 skip 不能算 passed，必须在报告中区分。