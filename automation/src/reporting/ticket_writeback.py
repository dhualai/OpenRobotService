"""Write automation run summaries back to tickets."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from automation.src.clients.api_client import ApiClient
from automation.src.runtime.diagnostics import diagnose_run
from automation.src.runtime.trace_store import TraceStore


def build_ticket_report(run_id: str, store: Optional[TraceStore] = None) -> Dict[str, Any]:
    """Build a compact report payload from a runtime run."""
    store = store or TraceStore()
    status = store.read_status(run_id) or {}
    diagnostics = diagnose_run(run_id, store=store)
    run_dir = store.run_dir(run_id)
    report_path = os.getenv(
        "ALLURE_REPORT_URL",
        str(run_dir / "artifacts" / "pytest.log"),
    )
    return {
        "run_id": run_id,
        "trace_id": status.get("trace_id"),
        "status": status.get("status"),
        "exit_code": status.get("exit_code"),
        "summary": status.get("summary"),
        "failure_class": diagnostics.get("failure_class"),
        "report_path": report_path,
        "run_dir": str(run_dir),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def writeback_ticket_report(
    ticket_id: int,
    run_id: str,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
    client: Optional[ApiClient] = None,
    store: Optional[TraceStore] = None,
) -> Dict[str, Any]:
    """PATCH ticket metadata with the automation run summary."""
    report = build_ticket_report(run_id, store=store)
    username = username or os.getenv("AUTOMATION_BOT_USERNAME", "")
    password = password or os.getenv("AUTOMATION_BOT_PASSWORD", "")
    if not username or not password:
        return {
            "status": "blocked",
            "ticket_id": ticket_id,
            "run_id": run_id,
            "error": "AUTOMATION_BOT_USERNAME/PASSWORD are required",
        }

    own_client = client is None
    client = client or ApiClient(raise_auth_errors=False)
    if own_client:
        await client.connect()

    try:
        login = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        if login.status_code != 200:
            return {"status": "failed", "ticket_id": ticket_id, "error": login.text}
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.patch(
            f"/api/tasks/{ticket_id}",
            headers=headers,
            json={"metadata_info": {"automation": report}},
        )
        return {
            "status": "updated" if response.status_code == 200 else "failed",
            "ticket_id": ticket_id,
            "http_status": response.status_code,
            "report": report,
            "response": response.text[:1000],
        }
    finally:
        if own_client:
            await client.close()