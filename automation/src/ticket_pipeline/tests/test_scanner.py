"""Unit tests for ticket scanner filtering."""

from automation.src.ticket_pipeline.models import TicketCandidate
from automation.src.ticket_pipeline.scanner import TicketScanner


class FakeTicketSource:
    def __init__(self, tickets):
        self.tickets = tickets

    def fetch(self, limit: int):
        return self.tickets[:limit]


def _ticket(ticket_id, *, project_id="Leo_test", status="new", task_type="feature", tags=None, created_by="user_a"):
    return TicketCandidate(
        id=ticket_id,
        title=f"ticket-{ticket_id}",
        description="desc",
        task_type=task_type,
        status=status,
        project_id=project_id,
        project_name="摇人吧服务号",
        tags=["auto_case"] if tags is None else tags,
        created_by=created_by,
    )


def test_scanner_filters_project_status_type_and_tag():
    source = FakeTicketSource(
        [
            _ticket(1),
            _ticket(2, status="resolved"),
            _ticket(3, task_type="problem"),
            _ticket(4, project_id="001"),
            _ticket(5, tags=["ai_generated"]),
            _ticket(6, task_type="BUG"),
        ]
    )
    result = TicketScanner(source).scan()
    assert [ticket.id for ticket in result] == [1, 6]


def test_scanner_excludes_robot_creators():
    source = FakeTicketSource([_ticket(1, created_by="automation_bot"), _ticket(2)])
    result = TicketScanner(source, exclude_creators=("automation_bot",)).scan()
    assert [ticket.id for ticket in result] == [2]
