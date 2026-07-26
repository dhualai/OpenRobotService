"""Base evaluator framework for LLM output quality assessment."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from automation.ai.utils.llm_client import LLMClient


@dataclass
class EvaluationResult:
    """Result of a single evaluation check.

    Attributes:
        score: Float between 0.0 and 1.0 indicating quality.
        passed: Whether the check passes (score >= threshold).
        details: Human-readable explanation of the evaluation.
    """
    score: float
    passed: bool
    details: str

    def to_dict(self) -> Dict[str, Any]:
        return {"score": self.score, "passed": self.passed, "details": self.details}


class BaseEvaluator(ABC):
    """Abstract base for all AI evaluators.

    Subclasses implement evaluate() to check a specific quality dimension
    (accuracy, hallucination, relevance, safety).
    """

    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    @property
    def llm(self) -> LLMClient:
        return self._llm

    @abstractmethod
    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        """Evaluate output_text quality relative to input_text and context."""
        ...
