"""Phase 4.5: Admin extension tests - daily reports, export, resources."""

import pytest
from automation.assertions import assert_status_code

pytestmark = pytest.mark.api


class TestDailyReports:
    async def test_create_daily_report(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/admin/daily-reports",
            headers=mock_auth_header, json={"type": "daily"})
        assert_status_code(r, 200)
        data = r.json()
        assert data["status"] == "generated"
        assert data["type"] == "daily"

    async def test_create_weekly_report(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/admin/daily-reports",
            headers=mock_auth_header, json={"type": "weekly"})
        assert_status_code(r, 200)
        assert r.json()["type"] == "weekly"


class TestExport:
    async def test_export_data(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/admin/export",
            headers=mock_auth_header, json={"format": "xlsx", "module": "tickets"})
        assert_status_code(r, 200)
        data = r.json()
        assert data["status"] == "processing"
        assert "task_id" in data


class TestResourceManagement:
    async def test_list_resources(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/resources",
            headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "items" in data

    async def test_create_resource(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/admin/resources",
            headers=mock_auth_header, json={"name": "test.docx", "type": "file"})
        assert_status_code(r, 200)
        assert r.json()["name"] == "test.docx"

    async def test_get_resource_detail(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/admin/resources/1",
            headers=mock_auth_header)
        assert_status_code(r, 200)
        assert r.json()["id"] == 1

    async def test_update_resource(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("PUT", "/api/admin/resources/1",
            headers=mock_auth_header, json={"name": "updated.docx"})
        assert_status_code(r, 200)
        assert r.json()["name"] == "updated.docx"
