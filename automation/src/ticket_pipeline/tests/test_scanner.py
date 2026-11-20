"""Unit tests for ticket scanner filtering."""

import pytest

from automation.src.ticket_pipeline.models import TicketCandidate
from automation.src.ticket_pipeline.scanner import SSHMySQLTicketSource, TicketScanner


class FakeTicketSource:
    def __init__(self, tickets):
        self.tickets = tickets

    def fetch(self, limit: int):
        return self.tickets[:limit]


def _ticket(
    ticket_id,
    *,
    project_id="Leo_test",
    status="new",
    task_type="feature",
    tags=None,
    created_by="user_a",
    source="manual",
):
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
        source=source,
    )


def test_scanner_has_no_tag_gate_by_default():
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
    assert [ticket.id for ticket in result] == [1, 5, 6]


def test_scanner_filters_by_tag_when_a_tag_is_configured():
    source = FakeTicketSource([_ticket(1, tags=["auto_case"]), _ticket(2, tags=["other"])])
    result = TicketScanner(source, tag="auto_case").scan()
    assert [ticket.id for ticket in result] == [1]


def test_scanner_excludes_ai_created_tickets():
    source = FakeTicketSource([_ticket(1, source="ai"), _ticket(2, source="manual")])
    result = TicketScanner(source).scan()
    assert [ticket.id for ticket in result] == [2]


def test_scanner_excludes_robot_creators():
    source = FakeTicketSource([_ticket(1, created_by="automation_bot"), _ticket(2)])
    result = TicketScanner(source, exclude_creators=("automation_bot",)).scan()
    assert [ticket.id for ticket in result] == [2]


def test_scanner_without_tag_filter_accepts_untagged_tickets():
    source = FakeTicketSource([_ticket(1, tags=[]), _ticket(2, tags=["ai_generated"])])
    result = TicketScanner(source, tag="").scan()
    assert [ticket.id for ticket in result] == [1, 2]


def test_scanner_can_target_resolved_status():
    source = FakeTicketSource([_ticket(1), _ticket(2, status="resolved")])
    result = TicketScanner(source, statuses=("resolved",)).scan()
    assert [ticket.id for ticket in result] == [2]


def test_scanner_can_target_single_task_type():
    source = FakeTicketSource([_ticket(1, task_type="feature"), _ticket(2, task_type="bug")])
    result = TicketScanner(source, task_types=("feature",)).scan()
    assert [ticket.id for ticket in result] == [1]


def test_ssh_source_reads_filters_from_env(monkeypatch):
    monkeypatch.setenv("PROD_TICKET_PROJECT_ID", "Leo_x")
    monkeypatch.setenv("PROD_TICKET_STATUSES", "new,resolved")
    monkeypatch.setenv("PROD_TICKET_TASK_TYPES", "feature")
    monkeypatch.setenv("PROD_TICKET_TAG", "")
    monkeypatch.setenv("PROD_TICKET_EXCLUDE_SOURCES", "ai,zentao")
    monkeypatch.setenv("PROD_TICKET_DB_PASSWORD", "secret")

    source = SSHMySQLTicketSource.from_env()

    assert source.project_id == "Leo_x"
    assert source.statuses == ("new", "resolved")
    assert source.task_types == ("feature",)
    assert source.tag == ""
    assert source.exclude_sources == ("ai", "zentao")


def test_ssh_source_defaults_to_no_tag_and_excludes_ai(monkeypatch):
    monkeypatch.delenv("PROD_TICKET_TAG", raising=False)
    monkeypatch.delenv("PROD_TICKET_EXCLUDE_SOURCES", raising=False)
    monkeypatch.setenv("PROD_TICKET_DB_PASSWORD", "secret")

    source = SSHMySQLTicketSource.from_env()

    assert source.tag == ""
    assert source.exclude_sources == ("ai",)


def test_ssh_source_builds_parameterized_scan_clause():
    source = SSHMySQLTicketSource(
        db_password="secret",
        project_id="Leo_x",
        statuses=("new", "resolved"),
        task_types=("feature", "bug"),
        tag="auto_case",
    )

    where, params = source._scan_clause()

    assert "project_id = %s" in where
    assert "status IN (%s, %s)" in where
    assert "task_type IN (%s, %s)" in where
    assert "JSON_CONTAINS" in where
    assert "NOT IN (%s)" in where
    assert params == ["Leo_x", "NEW", "RESOLVED", "FEATURE", "BUG", "auto_case", "ai"]


def test_ssh_source_scan_clause_omits_tag_when_disabled():
    source = SSHMySQLTicketSource(db_password="secret", tag="", task_types=("feature",))

    where, params = source._scan_clause()

    assert "JSON_CONTAINS" not in where
    assert "NOT IN (%s)" in where
    assert params == ["Leo_test", "NEW", "FEATURE", "ai"]


def test_ssh_source_requires_db_password():
    with pytest.raises(ValueError, match="PROD_TICKET_DB_PASSWORD"):
        SSHMySQLTicketSource(db_password="")
