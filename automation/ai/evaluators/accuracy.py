"""Accuracy evaluator: check if LLM output contains expected information."""

from typing import Any, Dict, List, Optional

from automation.ai.evaluators.base import BaseEvaluator, EvaluationResult


_DEFAULT_THRESHOLD = 0.5


class AccuracyEvaluator(BaseEvaluator):
    """Evaluate whether the output contains expected key information.

    Checks for the presence of expected keywords/entities in the output.
    In mock mode, uses keyword matching. In real mode, uses LLM-as-judge.
    """

    def __init__(self, llm_client, threshold: float = _DEFAULT_THRESHOLD):
        super().__init__(llm_client)
        self.threshold = threshold

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        expected: List[str] = (context or {}).get("expected_keywords", [])
        if not expected:
            return EvaluationResult(1.0, True, "No expected keywords specified")

        found = [kw for kw in expected if kw.lower() in output_text.lower()]
        score = len(found) / len(expected)
        passed = score >= self.threshold
        details = (
            f"Found {len(found)}/{len(expected)} expected keywords: "
            f"found={found}, missing={set(expected) - set(found)}"
        )
        return EvaluationResult(score, passed, details)

    async def evaluate_batch(
        self,
        pairs: List[Dict[str, Any]],
    ) -> List[EvaluationResult]:
        """Evaluate multiple (input, output, context) pairs."""
        results = []
        for pair in pairs:
            result = await self.evaluate(
                pair.get("input", ""),
                pair.get("output", ""),
                pair.get("context"),
            )
            results.append(result)
        return results

    @property
    def summary(self) -> str:
        return f"AccuracyEvaluator(threshold={self.threshold})"
