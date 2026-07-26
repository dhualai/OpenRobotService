"""AI test fixtures."""

import pytest

from automation.ai.utils.llm_client import LLMClient
from automation.ai.evaluators.accuracy import AccuracyEvaluator
from automation.ai.evaluators.hallucination import HallucinationEvaluator


@pytest.fixture
def mock_llm() -> LLMClient:
    """Return an LLMClient in mock mode (no API calls)."""
    return LLMClient(mock=True)


@pytest.fixture
def accuracy_evaluator(mock_llm: LLMClient) -> AccuracyEvaluator:
    return AccuracyEvaluator(mock_llm)


@pytest.fixture
def hallucination_evaluator(mock_llm: LLMClient) -> HallucinationEvaluator:
    return HallucinationEvaluator(mock_llm)
