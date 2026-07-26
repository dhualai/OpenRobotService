"""E2E test fixtures: shared mock client with multi-role tokens."""

import pytest
import httpx

from automation.mocks.backend_mock import create_mock_transport


@pytest.fixture
async def e2e_ctx():
    """Shared mock client with pre-logged-in tokens for all roles.

    Returns a dict with:
        client: httpx.AsyncClient (one transport shared across all requests)
        admin/hdr: Authorization header for admin
        engineer/hdr: Authorization header for engineer
        customer/hdr: Authorization header for customer
    """
    transport = create_mock_transport()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Pre-login all roles
        admin_r = await client.post("/auth/login", json={
            "username": "testadmin", "password": "admin123"})
        admin_tok = admin_r.json()["access_token"]

        eng_r = await client.post("/auth/login", json={
            "username": "engineer", "password": "eng123"})
        eng_tok = eng_r.json()["access_token"]

        cust_r = await client.post("/auth/login", json={
            "username": "customer", "password": "cust123"})
        cust_tok = cust_r.json()["access_token"]

        yield {
            "client": client,
            "admin_token": admin_tok,
            "engineer_token": eng_tok,
            "customer_token": cust_tok,
            "admin_hdr": {"Authorization": f"Bearer {admin_tok}"},
            "engineer_hdr": {"Authorization": f"Bearer {eng_tok}"},
            "customer_hdr": {"Authorization": f"Bearer {cust_tok}"},
        }
