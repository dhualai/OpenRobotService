"""AI backend endpoint tests: diagnose, discuss, summarize."""

import pytest
from automation.assertions import assert_status_code

pytestmark = pytest.mark.api


class TestAIDiagnose:
    async def test_diagnose_task(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/ai/task/diagnose",
            headers=mock_auth_header, json={"task_id": 1})
        assert_status_code(r, 200)
        data = r.json()
        assert "diagnosis" in data
        assert "confidence" in data


class TestAIDiscuss:
    async def test_discuss_task(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/ai/task/discuss",
            headers=mock_auth_header, json={"task_id": 1, "query": "What is the issue?"})
        assert_status_code(r, 200)
        data = r.json()
        assert "reply" in data
        assert "suggestions" in data


class TestAISummarize:
    async def test_summarize_task(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/ai/task/summarize",
            headers=mock_auth_header, json={"task_id": 1})
        assert_status_code(r, 200)
        data = r.json()
        assert "summary" in data
