from automation.src.ai_metrics.external_eval import (
    external_evaluator_status,
    run_deepeval,
    run_ragas,
)


def test_run_deepeval_with_fake_evaluator():
    result = run_deepeval(
        {"input": "问题", "actual_output": "回答"},
        evaluator=lambda case: {"scores": {"answer_relevancy": 0.9}, "passed": True},
    )

    assert result["available"] is True
    assert result["passed"] is True
    assert result["scores"]["answer_relevancy"] == 0.9


def test_run_ragas_with_fake_evaluator():
    result = run_ragas(
        [{"question": "q", "answer": "a", "contexts": ["c"], "ground_truth": "g"}],
        evaluator=lambda rows: {"scores": {"faithfulness": 0.8}, "passed": True},
    )

    assert result["available"] is True
    assert result["passed"] is True
    assert result["scores"]["faithfulness"] == 0.8


def test_external_evaluator_status_has_expected_keys():
    status = external_evaluator_status()
    assert set(status) == {"deepeval", "ragas"}