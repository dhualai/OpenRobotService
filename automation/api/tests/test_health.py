"""Smoke tests: API health check."""

import pytest


@pytest.mark.smoke
@pytest.mark.api
class TestHealth:
    """Health endpoint smoke tests."""

    async def test_health_check(self, api_client):
        """GET /api/health should return 200 with healthy status."""
        response = await api_client.request("GET", "/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    async def test_health_response_structure(self, api_client):
        """Health response should have expected fields."""
        response = await api_client.request("GET", "/health")
        data = response.json()
        assert isinstance(data, dict)
        assert "status" in data
