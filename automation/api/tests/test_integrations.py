"""Phase 5: Integration API tests.

External source list and task-user mapping management.
Uses mock backend (no real infrastructure needed).
"""

import pytest

from automation.assertions import assert_status_code


pytestmark = pytest.mark.api


class TestIntegrations:
    """Integration source management."""

    async def test_list_sources(self, mock_api_client, mock_auth_header):
        """GET /api/integrations."""
        r = await mock_api_client.request("GET", "/api/integrations", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert data[0]["name"] == "wecom"


class TestTaskUserMapping:
    """Task-user mapping management."""

    async def test_create_mapping(self, mock_api_client, mock_auth_header):
        """POST /api/admin/task-user-mappings."""
        payload = {"source_task_id": "ext_001", "local_task_id": 1}
        r = await mock_api_client.request("POST", "/api/admin/task-user-mappings",
            headers=mock_auth_header, json=payload)
        assert_status_code(r, 200)
        data = r.json()
        assert data["source_task_id"] == "ext_001"
        assert data["id"] > 0
