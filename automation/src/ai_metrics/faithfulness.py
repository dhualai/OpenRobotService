"""L2 faithfulness metric: is the answer grounded in the reference docs?

The answer is judged against reference documents provided by the golden
case (representative knowledge base content for the scenario). The judge
checks the answer does not contradict the docs and does not introduce
unsupported claims (anti-hallucination). LLM dependency is injected so
the metric stays unit-testable with a fake judge.
"""

import json
import re
from typing import Any, Dict, List


def _extract_json(text: str) -> Dict[str, Any]:
    fenced = re.search(r"\{[\s\S]*\}", text)
    if not fenced:
        return {"score": 0.0, "reason": f"unparsable judge output: {text[:120]}"}
    try:
        return json.loads(fenced.group())
    except json.JSONDecodeError:
        return {"score": 0.0, "reason": f"unparsable judge output: {text[:120]}"}


def build_faithfulness_prompt(question: str, answer: str, docs: List[str]) -> str:
    """Build the user prompt for the faithfulness judge."""
    doc_text = "\n\n".join(f"--- 文档 {i + 1} ---\n{d}" for i, d in enumerate(docs))
    return (
        f"## 参考文档\n{doc_text}\n\n"
        f"## 用户问题\n{question}\n\n"
        f"## AI 回答\n{answer}\n\n"
        "检查该回答是否与参考文档一致：是否存在文档中不存在的断言、"
        "是否歪曲了文档内容、是否编造检查项或结论。"
    )


async def judge_faithfulness(
    question: str,
    answer: str,
    docs: List[str],
    judge,
) -> Dict[str, Any]:
    """Judge whether the answer is faithful to the reference docs.

    Args:
        judge: object with async complete(system_prompt, user_prompt) -> str.

    Returns:
        {"score": float 0-1, "reason": str}. Score 1.0 == fully grounded.
    """
    if not docs:
        return {"score": 1.0, "reason": "no reference docs provided (skipped)"}
    system_prompt = (
        "你是一个严格的防幻觉评审员。根据参考文档判断 AI 回答是否忠实，"
        "只输出 JSON：{\"score\": 0到1之间的小数, \"reason\": \"一句话理由\"}。"
        "score=1 表示回答完全基于文档、无编造；score=0 表示严重编造或歪曲。"
    )
    raw = await judge.complete(system_prompt, build_faithfulness_prompt(question, answer, docs))
    parsed = _extract_json(raw)
    try:
        score = float(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    return {"score": max(0.0, min(1.0, score)), "reason": parsed.get("reason", "")}
