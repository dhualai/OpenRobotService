"""E2E critical path tests: complete user journeys across modules.

Uses shared e2e_ctx fixture that provides one mock backend instance
with pre-logged-in tokens for admin, engineer, and customer roles.
"""
import pytest

from automation.e2e.flows.ticket_to_task import TicketFlow
from automation.e2e.flows.wechat_qa import WeChatQAFlow

pytestmark = pytest.mark.e2e


class TestTicketLifecycle:
    """Flow 1: Ticket create → assign → process → resolve → close."""

    async def test_full_ticket_lifecycle(self, e2e_ctx):
        """Complete ticket lifecycle with 4 role transitions."""
        c = e2e_ctx
        a, e = c["admin_hdr"], c["engineer_hdr"]

        # 1. Admin creates ticket
        ticket = await TicketFlow.create_ticket(c, "admin_hdr",
                                                 title="Robot malfunction",
                                                 description="Error code E1001",
                                                 tags=["urgent", "robot"])
        tid = ticket["id"]
        assert ticket["status"] == "pending"
        assert ticket["title"] == "Robot malfunction"

        # 2. Engineer assigns to self and starts processing
        await TicketFlow.assign_ticket(c, "engineer_hdr", tid)
        await TicketFlow.change_status(c, "engineer_hdr", tid, "in_progress")
        ticket = await TicketFlow.get_ticket(c, "admin_hdr", tid)
        assert ticket["status"] == "in_progress"
        assert ticket["assigned_to"] is not None

        # 3. Engineer adds diagnostic comments
        await TicketFlow.add_comment(c, "engineer_hdr", tid,
                                      "Investigating error code E1001")
        await TicketFlow.add_comment(c, "engineer_hdr", tid,
                                      "Found faulty sensor, replaced")

        # 4. Engineer resolves, admin closes
        await TicketFlow.change_status(c, "engineer_hdr", tid, "resolved")
        await TicketFlow.change_status(c, "admin_hdr", tid, "closed")

        ticket = await TicketFlow.get_ticket(c, "admin_hdr", tid)
        assert ticket["status"] == "closed"

        comments = await TicketFlow.get_comments(c, "admin_hdr", tid)
        assert len(comments) >= 2

    async def test_invalid_status_transition_blocked(self, e2e_ctx):
        """Cannot skip from pending directly to closed."""
        ticket = await TicketFlow.create_ticket(e2e_ctx, "admin_hdr",
                                                 title="Status test")
        r = await e2e_ctx["client"].request(
            "PATCH", f"/api/tasks/{ticket['id']}/status",
            headers=e2e_ctx["admin_hdr"], json={"status": "closed"})
        assert r.status_code == 400


@pytest.mark.e2e
class TestQAIntegration:
    """Flow 2: QA → Conversation → Ticket."""

    async def test_qa_to_conversation_flow(self, e2e_ctx):
        """Customer asks question, creates conversation."""
        # 1. Customer asks a QA question
        answer = await WeChatQAFlow.ask_question(
            e2e_ctx, "customer_hdr",
            "How do I reset my robot?")
        assert answer["success"] is True
        assert "reset" in answer["answer"].lower()

        # 2. Customer creates a conversation
        conv = await WeChatQAFlow.create_conversation(
            e2e_ctx, "customer_hdr",
            title="Reset help")
        assert conv["id"] > 0
        assert conv["title"] == "Reset help"

    async def test_create_ticket_after_qa(self, e2e_ctx):
        """Engineer creates a ticket after customer QA session."""
        # Simulate: QA happened → engineer creates ticket
        ticket = await TicketFlow.create_ticket(
            e2e_ctx, "engineer_hdr",
            title="Follow-up on QA: robot reset issue",
            description="Customer reported reset problems",
            tags=["qa-followup"])
        assert ticket["status"] == "pending"
        assert ticket["created_by"] == "engineer"


@pytest.mark.e2e
class TestMultiRole:
    """Flow 3: Multi-role ticket collaboration."""

    async def test_multi_role_collaboration(self, e2e_ctx):
        """Admin creates → Customer views → Engineer processes."""
        # 1. Admin creates ticket
        ticket = await TicketFlow.create_ticket(
            e2e_ctx, "admin_hdr",
            title="Multi-role test",
            description="Testing cross-role workflow")
        tid = ticket["id"]

        # 2. Customer views their tasks (list)
        r = await e2e_ctx["client"].request(
            "GET", "/api/my-tasks/",
            headers=e2e_ctx["customer_hdr"])
        assert r.status_code == 200
        assert "items" in r.json()

        # 3. Engineer takes over
        await TicketFlow.assign_ticket(e2e_ctx, "engineer_hdr", tid)
        await TicketFlow.change_status(e2e_ctx, "engineer_hdr", tid, "in_progress")

        ticket = await TicketFlow.get_ticket(e2e_ctx, "admin_hdr", tid)
        assert ticket["assigned_to"] is not None
        assert ticket["status"] == "in_progress"

    async def test_ai_assign_after_ticket_creation(self, e2e_ctx):
        """Admin creates ticket, requests AI auto-assign."""
        ticket = await TicketFlow.create_ticket(
            e2e_ctx, "admin_hdr",
            title="AI assign test")
        tid = ticket["id"]

        r = await e2e_ctx["client"].request(
            "POST", f"/api/tasks/{tid}/ai-assign",
            headers=e2e_ctx["admin_hdr"])
        assert r.status_code == 200
        data = r.json()
        assert data["assigned_to"] is not None
        assert data["confidence"] > 0
