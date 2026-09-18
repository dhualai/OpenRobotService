"""AiDataAnalysisPlatform evaluation (L1 + L3, data-driven).

Runs against the real AI service via POST /api/ai/analysis/analyze;
skips when the service is unreachable.
"""

import pytest

from automation.tests.ai.runner import load_ai_cases, run_analysis_case

CASES = load_ai_cases("data_analysis")


@pytest.mark.ai
class TestDataAnalysisEval:
    @pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
    async def test_analyze(self, ai_client, case):
        result = await run_analysis_case(ai_client, case, suite="data_analysis")
        if not result["passed"]:
            pytest.fail(f"{case['id']} analysis failed: {result['detail']}")
        if result.get("skipped"):
            detail = "; ".join(f"{c['layer']}:{c['metric']}" for c in result["skipped"])
            pytest.skip(f"L3 judge unavailable: {detail}")
