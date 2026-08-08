"""AiDiagnosisPlatform evaluation (L1 deterministic layer, data-driven)."""

import pytest

from automation.tests.ai.runner import load_ai_cases, run_ai_case

CASES = load_ai_cases("diagnosis")


@pytest.mark.ai
class TestDiagnosisEval:
    @pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
    async def test_eval(self, ai_client, case):
        result = await run_ai_case(ai_client, case)
        assert result.passed, f"{result.case_id} failed: {result.summary}"
