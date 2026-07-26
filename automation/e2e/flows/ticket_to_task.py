"""Ticket lifecycle E2E flow helper."""

from typing import Any, Dict, Optional


class TicketFlow:
    """Reusable ticket lifecycle operations for E2E tests.

    All methods take (ctx, auth_header) and return response data dicts,
    so tests can compose them flexibly.
    """

    HEADERS = ["admin_hdr", "engineer_hdr", "customer_hdr"]

    @staticmethod
    async def create_ticket(ctx, auth_header: str, **overrides) -> Dict[str, Any]:
        """Create a ticket and return its data."""
        payload = {
            "title": overrides.get("title", "E2E test ticket"),
            "description": overrides.get("description", "Created by E2E test"),
            "priority": overrides.get("priority", "high"),
            "tags": overrides.get("tags", ["e2e"]),
        }
        r = await ctx["client"].request("POST", "/api/tasks",
                                          headers=ctx[auth_header], json=payload)
        assert r.status_code == 200, f"Create ticket failed: {r.text}"
        return r.json()

    @staticmethod
    async def get_ticket(ctx, auth_header: str, ticket_id: int) -> Dict[str, Any]:
        r = await ctx["client"].request("GET", f"/api/tasks/{ticket_id}",
                                          headers=ctx[auth_header])
        assert r.status_code == 200
        return r.json()

    @staticmethod
    async def assign_ticket(ctx, auth_header: str, ticket_id: int,
                            assignee: str = "engineer-01") -> Dict[str, Any]:
        r = await ctx["client"].request("PATCH", f"/api/tasks/{ticket_id}/assign",
                                          headers=ctx[auth_header],
                                          json={"assigned_to": assignee})
        assert r.status_code == 200
        return r.json()

    @staticmethod
    async def change_status(ctx, auth_header: str, ticket_id: int,
                            status: str) -> Dict[str, Any]:
        r = await ctx["client"].request("PATCH", f"/api/tasks/{ticket_id}/status",
                                          headers=ctx[auth_header],
                                          json={"status": status})
        assert r.status_code == 200, f"Status change to {status} failed: {r.text}"
        return r.json()

    @staticmethod
    async def add_comment(ctx, auth_header: str, ticket_id: int,
                          content: str) -> Dict[str, Any]:
        r = await ctx["client"].request("POST", f"/api/tasks/{ticket_id}/comments",
                                          headers=ctx[auth_header],
                                          json={"content": content})
        assert r.status_code == 201
        return r.json()

    @staticmethod
    async def get_comments(ctx, auth_header: str, ticket_id: int) -> list:
        r = await ctx["client"].request("GET", f"/api/tasks/{ticket_id}/comments",
                                          headers=ctx[auth_header])
        assert r.status_code == 200
        return r.json()
