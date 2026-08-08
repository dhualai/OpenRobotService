"""Assigner evaluation (L1, data-driven).

Executes via ai.agents...assign_ticket; requires the AI runtime
dependencies. Skips with a clear message when those are unavailable.
"""

import pytest

from automation.tests.ai.runner import load_ai_cases, run_assigner_case

CASES = load_ai_cases("assigner")


@pytest.mark.ai
class TestAssignerEval:
    @pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
    async def test_assign(self, case):
        result = await run_assigner_case(case)
        if result.get("skipped_all"):
            pytest.skip(result.get("detail", "AI runtime deps unavailable"))
        assert result["passed"], f"{case['id']} assigner check failed: {result['detail']}"
