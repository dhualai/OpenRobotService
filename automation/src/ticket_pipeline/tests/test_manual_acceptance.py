from types import SimpleNamespace

import pytest

from automation.src.ticket_pipeline import manual_acceptance
from automation.src.ticket_pipeline.scanner import SSHMySQLTicketSource


@pytest.mark.parametrize(("status", "expected_exit", "eligible"), [
    ("resolved", 0, "true"),
    ("closed", 0, "true"),
    ("new", 0, "false"),
    (None, 1, None),
])
def test_manual_lookup_sets_eligibility(monkeypatch, tmp_path, status, expected_exit, eligible):
    output = tmp_path / "output"
    key = tmp_path / "key"
    key.write_text("test", encoding="utf-8")
    for name in manual_acceptance.REQUIRED_ENV:
        monkeypatch.setenv(name, "configured")
    monkeypatch.setenv("PROD_TICKET_SSH_KEY", str(key))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("TICKET_ID", "1053")
    monkeypatch.setenv("TICKET_PROJECT_ID", "Leo_test")
    queried = []

    def lookup(ticket_id, project_id):
        queried.append((ticket_id, project_id))
        return status

    monkeypatch.setattr(SSHMySQLTicketSource, "from_env", lambda: SimpleNamespace(fetch_status_by_id=lookup))
    assert manual_acceptance.main() == expected_exit
    assert queried == [(1053, "Leo_test")]
    if eligible is None:
        assert not output.exists()
    else:
        assert output.read_text(encoding="utf-8") == f"eligible={eligible}\n"


def test_manual_lookup_requires_explicit_database_user(monkeypatch):
    monkeypatch.setenv("TICKET_ID", "1053")
    monkeypatch.setenv("TICKET_PROJECT_ID", "Leo_test")
    monkeypatch.delenv("PROD_TICKET_DB_USER", raising=False)
    assert manual_acceptance.main() == 1


def test_status_query_only_reads_the_bound_ticket_status(monkeypatch):
    source = SSHMySQLTicketSource(db_password="test-only")

    def fetch(sql, params):
        assert sql == "SELECT status FROM tasks WHERE id = %s AND project_id = %s"
        assert params == (1053, "Leo_test")
        return [{"status": "resolved"}]

    monkeypatch.setattr(source, "_fetch_rows", fetch)
    assert source.fetch_status_by_id(1053, "Leo_test") == "resolved"
