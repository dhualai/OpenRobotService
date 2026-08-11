"""RAG retrieval recall evaluation (L2, data-driven).

Executes via ai.core.retrieval; requires the AI runtime dependencies.
Skips with a clear message when those are unavailable.
"""

import pytest

from automation.tests.ai.runner import load_ai_cases, run_rag_case

CASES = load_ai_cases("rag_retrieval")


@pytest.mark.ai
class TestRagRetrievalEval:
    @pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
    async def test_recall(self, case):
        result = await run_rag_case(case)
        if result.get("skipped_all"):
            pytest.skip(result.get("detail", "AI runtime deps unavailable"))
        assert result["passed"], f"{case['id']} recall failed: {result['detail']}"
