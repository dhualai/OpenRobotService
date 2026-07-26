"""Data-driven API tests."""
import pytest
from automation.tests.common.test_runner import load_cases, run_case

CASES = load_cases("call")

@pytest.mark.api
class TestCallApi:
    @pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
    async def test_api(self, mock_api_client, mock_auth_header, case):
        await run_case(mock_api_client, mock_auth_header, case)
