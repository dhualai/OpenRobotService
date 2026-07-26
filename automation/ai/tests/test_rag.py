"""Scenario-based RAG tests: load YAML scenarios, run through evaluators."""

import os
from pathlib import Path

import pytest
import yaml

from automation.ai import LLMClient
from automation.ai.evaluators.accuracy import AccuracyEvaluator
from automation.ai.evaluators.hallucination import HallucinationEvaluator

_SCENARIO_DIR = Path(__file__).parents[1] / "scenarios"


def _load_scenarios(name: str) -> list:
    path = _SCENARIO_DIR / name
    if not path.exists():
        pytest.skip(f"Scenario file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("scenarios", [])


@pytest.mark.ai
class TestRAGScenarios:
    """Load RAG scenarios and verify evaluator scoring."""

    @pytest.fixture(scope="class")
    def llm(self) -> LLMClient:
        return LLMClient(mock=True)

    @pytest.fixture(scope="class")
    def accuracy(self, llm) -> AccuracyEvaluator:
        return AccuracyEvaluator(llm)

    @pytest.fixture(scope="class")
    def hallucination(self, llm) -> HallucinationEvaluator:
        return HallucinationEvaluator(llm)

    def test_scenarios_loaded(self):
        scenarios = _load_scenarios("rag_scenarios.yaml")
        assert len(scenarios) >= 4, f"Expected >=4 scenarios, got {len(scenarios)}"

    async def test_rag_001_basic_error_code(self, accuracy, hallucination):
        scenarios = _load_scenarios("rag_scenarios.yaml")
        s = next(sc for sc in scenarios if sc["id"] == "rag-001")
        result = await accuracy.evaluate(
            s["query"], "Error E1001 is a sensor fault.", s)
        assert result.passed, f"Accuracy failed: {result.details}"

    async def test_rag_002_reset_procedure(self, accuracy):
        scenarios = _load_scenarios("rag_scenarios.yaml")
        s = next(sc for sc in scenarios if sc["id"] == "rag-002")
        result = await accuracy.evaluate(
            s["query"], "Press power to reset the robot.", s)
        assert result.passed, f"Accuracy failed: {result.details}"

    async def test_rag_003_maintenance_schedule(self, accuracy, hallucination):
        scenarios = _load_scenarios("rag_scenarios.yaml")
        s = next(sc for sc in scenarios if sc["id"] == "rag-003")
        acc_result = await accuracy.evaluate(
            s["query"], "Check maintenance schedule regularly.", s)
        assert acc_result.passed

    async def test_rag_005_multi_part_diagnostic(self, accuracy):
        scenarios = _load_scenarios("rag_scenarios.yaml")
        s = next(sc for sc in scenarios if sc["id"] == "rag-005")
        result = await accuracy.evaluate(
            s["query"], "Check error code first, then run diagnostic.", s)
        assert result.passed
