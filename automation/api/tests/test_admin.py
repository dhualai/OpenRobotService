"""Phase 4: Admin API tests.

Admin tickets, projects, risks, dashboard, users, roles.
Uses mock backend (no real infrastructure needed).
"""

import pytest

from automation.assertions import assert_status_code


pytestmark = pytest.mark.api


class TestAdminTickets:
    """GET /api/admin/tickets — Admin ticket management."""

    async def test_list_tickets(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/tickets", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "items" in data
        assert "total" in data

    async def test_ticket_stats(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/tickets/stats", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "total" in data
        assert "by_status" in data


class TestAdminProjects:
    """Admin project management."""

    async def test_list_projects(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/projects", headers=mock_auth_header)
        assert_status_code(r, 200)
        assert isinstance(r.json(), list)

    async def test_create_project(self, mock_api_client, mock_auth_header):
        data = {"name": "Test Project", "description": "A test project"}
        r = await mock_api_client.request("POST", "/api/admin/projects",
            headers=mock_auth_header, json=data)
        assert_status_code(r, 200)
        result = r.json()
        assert result["name"] == "Test Project"
        assert result["id"] > 0


class TestAdminRisks:
    """Admin risk management."""

    async def test_list_risks(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/projects/risks",
            headers=mock_auth_header)
        assert_status_code(r, 200)
        assert isinstance(r.json(), list)


class TestAdminDashboard:
    """Admin dashboard."""

    async def test_dashboard_summary(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/dashboard/tickets/summary",
            headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "total_tickets" in data
        assert "by_status" in data


class TestAdminUsers:
    """Admin user management."""

    async def test_list_users(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/users/", headers=mock_auth_header)
        assert_status_code(r, 200)
        users = r.json()
        assert isinstance(users, list)
        assert len(users) > 0


class TestAdminRoles:
    """Admin role management."""

    async def test_list_roles(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/roles/", headers=mock_auth_header)
        assert_status_code(r, 200)
        roles = r.json()
        assert isinstance(roles, list)
        assert len(roles) > 0
