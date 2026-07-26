"""AI evaluation module.

Provides LLM client abstraction, evaluator framework for accuracy/hallucination
checks, and scenario-based test data for RAG, task agent, and WeChat AI.
"""

from automation.ai.utils.llm_client import LLMClient
from automation.ai.evaluators.base import EvaluationResult, BaseEvaluator
from automation.ai.evaluators.accuracy import AccuracyEvaluator
from automation.ai.evaluators.hallucination import HallucinationEvaluator

__all__ = [
    "LLMClient",
    "EvaluationResult",
    "BaseEvaluator",
    "AccuracyEvaluator",
    "HallucinationEvaluator",
]
