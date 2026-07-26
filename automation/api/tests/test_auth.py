"""Auth module API tests: login, token validation, error handling."""

import pytest

from automation.assertions import assert_status_code


@pytest.mark.smoke
@pytest.mark.api
class TestLogin:
    """Login endpoint tests."""

    async def test_login_success(self, api_client):
        """POST /auth/login with valid credentials should return JWT token."""
        login_data = {"username": "testadmin", "password": "admin123"}
        response = await api_client.request("POST", "/auth/login", json=login_data)
        assert_status_code(response, 200)
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, api_client):
        """POST /auth/login with wrong password should return 401."""
        login_data = {"username": "testadmin", "password": "wrong_password"}
        response = await api_client.request("POST", "/auth/login", json=login_data)
        assert response.status_code in (401, 403)

    async def test_login_user_not_found(self, api_client):
        """POST /auth/login with non-existent username should return 401."""
        login_data = {"username": "nonexistent_user_xyz", "password": "password123"}
        response = await api_client.request("POST", "/auth/login", json=login_data)
        assert response.status_code in (401, 403)

    async def test_login_empty_username(self, api_client):
        """POST /auth/login with empty fields should return 422."""
        login_data = {"username": "", "password": ""}
        response = await api_client.request("POST", "/auth/login", json=login_data)
        assert response.status_code == 422

    async def test_login_missing_fields(self, api_client):
        """POST /auth/login with missing fields should return 422."""
        response = await api_client.request("POST", "/auth/login", json={})
        assert response.status_code == 422


@pytest.mark.api
class TestAuthToken:
    """Token validation and auth header tests."""

    async def test_get_current_user_with_valid_token(self, api_client, auth_header):
        """GET /auth/me with valid token should return user info."""
        response = await api_client.request("GET", "/auth/me", headers=auth_header)
        assert_status_code(response, 200)
        data = response.json()
        assert "username" in data
        assert data["username"] == "testadmin"

    async def test_get_current_user_no_token(self, api_client):
        """GET /auth/me without token should return 401."""
        response = await api_client.request("GET", "/auth/me")
        assert response.status_code == 401

    async def test_get_current_user_forged_token(self, api_client):
        """GET /auth/me with forged token should return 401."""
        forged_headers = {"Authorization": "Bearer forged.token.here"}
        response = await api_client.request("GET", "/auth/me", headers=forged_headers)
        assert response.status_code == 401
