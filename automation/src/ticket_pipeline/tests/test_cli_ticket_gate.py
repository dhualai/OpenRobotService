"""Tests for the ticket gate CLI safety guards."""

from __future__ import annotations

import runpy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_PATH = REPO_ROOT / "automation" / "scripts" / "cli-ticket-gate.py"


def test_status_lookup_is_disabled_when_production_credentials_are_missing(monkeypatch):
    monkeypatch.setenv("TICKET_STATUS_LOOKUP_DISABLED", "1")
    module = runpy.run_path(str(CLI_PATH))

    statuses, error = module["_load_statuses"]([837])

    assert statuses is None
    assert "disabled" in error
