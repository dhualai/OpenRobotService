"""Tests for AI evaluators."""

import pytest

from automation.ai.evaluators.accuracy import AccuracyEvaluator
from automation.ai.evaluators.hallucination import HallucinationEvaluator


class TestAccuracyEvaluator:
    async def test_all_keywords_found(self, accuracy_evaluator):
        result = await accuracy_evaluator.evaluate(
            "What is error E1001?",
            "Error code E1001 indicates a sensor fault in the robot.",
            {"expected_keywords": ["E1001", "sensor", "fault"]},
        )
        assert result.passed is True
        assert result.score == 1.0

    async def test_partial_keywords_found(self, accuracy_evaluator):
        result = await accuracy_evaluator.evaluate(
            "How to reset?",
            "Press the power button.",
            {"expected_keywords": ["power", "reset", "button"]},
        )
        assert result.score == 2 / 3
        assert result.passed is True

    async def test_no_keywords_found(self, accuracy_evaluator):
        result = await accuracy_evaluator.evaluate(
            "Hello",
            "Hi there!",
            {"expected_keywords": ["robot", "error", "fix"]},
        )
        assert result.score == 0.0
        assert result.passed is False

    async def test_no_expected_keywords(self, accuracy_evaluator):
        result = await accuracy_evaluator.evaluate("Hi", "Hello!", {})
        assert result.score == 1.0
        assert result.passed is True

    async def test_evaluate_batch(self, accuracy_evaluator):
        pairs = [
            {"input": "Q1", "output": "Error E1001", "context": {"expected_keywords": ["E1001"]}},
            {"input": "Q2", "output": "Just hello", "context": {"expected_keywords": ["robot"]}},
        ]
        results = await accuracy_evaluator.evaluate_batch(pairs)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False


class TestHallucinationEvaluator:
    async def test_clean_response(self, hallucination_evaluator):
        result = await hallucination_evaluator.evaluate(
            "What is the status?",
            "The robot is currently operating normally. All systems are functional.",
        )
        assert result.passed is True
        assert result.score >= 0.7

    async def test_hedging_response(self, hallucination_evaluator):
        result = await hallucination_evaluator.evaluate(
            "What caused the fault?",
            "Based on my knowledge, I'm not certain about the root cause. "
            "I don't have access to the diagnostic logs. "
            "I cannot confirm without more data.",
        )
        assert result.passed is False
        assert result.score < 0.7

    async def test_mixed_response(self, hallucination_evaluator):
        result = await hallucination_evaluator.evaluate(
            "Diagnose error 2024",
            "The error occurred in 2024. According to my training data, "
            "this is a known issue. I don't have access to the latest logs.",
        )
        assert isinstance(result.score, float)
        assert len(result.details) > 0

    async def test_summary_property(self, accuracy_evaluator, hallucination_evaluator):
        assert "AccuracyEvaluator" in accuracy_evaluator.summary
        assert "HallucinationEvaluator" in hallucination_evaluator.summary


class TestEvaluationResult:
    async def test_to_dict(self):
        from automation.ai.evaluators.base import EvaluationResult
        r = EvaluationResult(score=0.8, passed=True, details="Good")
        d = r.to_dict()
        assert d["score"] == 0.8
        assert d["passed"] is True
        assert d["details"] == "Good"
