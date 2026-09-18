"""Optional external evaluators: DeepEval and Ragas.

The adapters are injectable so the core framework remains usable when the
optional packages are not installed. Install them with:

    pip install -e "automation/[external-eval]"
"""

from __future__ import annotations

import importlib.util
from typing import Any, Callable, Dict, List, Optional


def deepeval_available() -> bool:
    return importlib.util.find_spec("deepeval") is not None


def ragas_available() -> bool:
    return importlib.util.find_spec("ragas") is not None


def external_evaluator_status() -> Dict[str, bool]:
    return {"deepeval": deepeval_available(), "ragas": ragas_available()}


def _normalize_result(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        scores = raw.get("scores") or {}
        passed = raw.get("passed")
        return {
            "available": True,
            "passed": bool(passed) if passed is not None else all(float(v) >= 0.5 for v in scores.values()),
            "scores": scores,
            "raw": raw.get("raw", str(raw)[:4000]),
        }

    test_results = getattr(raw, "test_results", None) or []
    scores: Dict[str, float] = {}
    passed = True
    for result in test_results:
        for metric in getattr(result, "metrics_data", []) or []:
            name = getattr(metric, "name", metric.__class__.__name__)
            score = getattr(metric, "score", None)
            if score is not None:
                scores[str(name)] = float(score)
            success = getattr(metric, "success", None)
            if success is False:
                passed = False
    return {"available": True, "passed": passed, "scores": scores, "raw": str(raw)[:4000]}


def _load_deepeval_evaluator() -> Callable[[Dict[str, Any]], Any]:
    try:
        from deepeval import evaluate
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:
        raise ImportError(f"deepeval is not installed: {exc}") from exc

    def _evaluate(case: Dict[str, Any]) -> Any:
        test_case = LLMTestCase(
            input=case["input"],
            actual_output=case["actual_output"],
            retrieval_context=case.get("retrieval_context"),
            expected_output=case.get("expected_output"),
        )
        selected = set(case.get("metrics", ["answer_relevancy", "faithfulness"]))
        threshold = float(case.get("threshold", 0.7))
        metric_map = {
            "answer_relevancy": AnswerRelevancyMetric,
            "faithfulness": FaithfulnessMetric,
        }
        metrics = [metric_map[name](threshold=threshold) for name in selected if name in metric_map]
        return evaluate(test_cases=[test_case], metrics=metrics)

    return _evaluate


def run_deepeval(
    case: Dict[str, Any],
    evaluator: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    """Run DeepEval on one LLM test case."""
    try:
        evaluator = evaluator or _load_deepeval_evaluator()
    except Exception as exc:  # noqa: BLE001 - optional dependency
        return {"available": False, "skipped": True, "error": str(exc), "scores": {}}
    return _normalize_result(evaluator(case))


def _load_ragas_evaluator() -> Callable[[List[Dict[str, Any]]], Any]:
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    except ImportError as exc:
        raise ImportError(f"ragas or datasets is not installed: {exc}") from exc

    def _evaluate(rows: List[Dict[str, Any]]) -> Any:
        dataset = Dataset.from_list(rows)
        return evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        )

    return _evaluate


def run_ragas(
    rows: List[Dict[str, Any]],
    evaluator: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
) -> Dict[str, Any]:
    """Run Ragas over a list of RAG evaluation rows."""
    try:
        evaluator = evaluator or _load_ragas_evaluator()
    except Exception as exc:  # noqa: BLE001 - optional dependency
        return {"available": False, "skipped": True, "error": str(exc), "scores": {}}
    return _normalize_result(evaluator(rows))