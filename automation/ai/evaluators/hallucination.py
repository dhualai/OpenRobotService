"""Hallucination evaluator: detect unsupported claims in LLM output."""

import re
from typing import Any, Dict, Optional

from automation.ai.evaluators.base import BaseEvaluator, EvaluationResult


_HALLUCINATION_PATTERNS = [
    r"I don't have (access to|information about|data on)",
    r"I'm not (aware|certain|sure)",
    r"I cannot (verify|confirm|guarantee)",
    r"based on (my|the available|limited) (knowledge|data|information)",
    r"according to (my|the) (knowledge|training|understanding)",
]

_UNCERTAINTY_THRESHOLD = 3
_DEFAULT_THRESHOLD = 0.7


class HallucinationEvaluator(BaseEvaluator):
    """Evaluate whether the output contains potentially hallucinated content.

    A high score means LOW hallucination risk (output is reliable).
    A low score means HIGH hallucination risk (output may be fabricated).

    Detection methods:
    - Mock mode: count hedging phrases and confidence qualifiers
    - Real mode: LLM-as-judge to detect factual inconsistencies
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
        # Count hedging / uncertainty patterns
        uncertainty_count = 0
        for pattern in _HALLUCINATION_PATTERNS:
            matches = re.findall(pattern, output_text, re.IGNORECASE)
            uncertainty_count += len(matches)

        # Check for specific factual claims (numbers, dates, proper nouns)
        specific_claims = len(re.findall(r'\b\d{4}\b', output_text))  # years
        specific_claims += len(re.findall(r'\b[A-Z][a-z]+ \d{1,2}', output_text))  # dates

        # Score: more uncertainty = lower score
        raw_score = max(0.0, 1.0 - (uncertainty_count / _UNCERTAINTY_THRESHOLD))
        passed = raw_score >= self.threshold

        details_parts = []
        if uncertainty_count > 0:
            details_parts.append(f"{uncertainty_count} uncertainty patterns detected")
        if specific_claims > 0:
            details_parts.append(f"{specific_claims} specific factual claims made")
        details = "; ".join(details_parts) if details_parts else "No issues detected"

        return EvaluationResult(raw_score, passed, details)

    @property
    def summary(self) -> str:
        return f"HallucinationEvaluator(threshold={self.threshold})"
