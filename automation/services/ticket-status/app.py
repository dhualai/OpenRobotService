"""Read-only ticket status API for CI.

Runs on the cloud server next to MySQL and answers exactly one question:
"is this ticket resolved/closed?" CI calls it over HTTPS with a bearer token.

Design constraints:
- read-only: only SELECT, only the columns CI needs;
- single purpose: no arbitrary SQL, no table names from the caller;
- fails closed: missing token or DB error returns 5xx, never a fake status.

Env:
  TICKET_API_TOKEN   required, bearer token CI must present
  TICKET_DB_HOST     default 127.0.0.1
  TICKET_DB_PORT     default 3306
  TICKET_DB_NAME     default helpdesk_724
  TICKET_DB_USER     default openrobot_ro
  TICKET_DB_PASSWORD required
"""

from __future__ import annotations

import os
import re
from typing import Any

import pymysql
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

APP_VERSION = "1.0.0"
TICKET_ID = re.compile(r"^(ORS-)?(\d{1,10})$")
ALLOWED_STATUSES = {"resolved", "closed", "pending", "processing", "new"}

app = FastAPI(title="ticket-status", version=APP_VERSION, docs_url=None, redoc_url=None)


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if not value and not default:
        raise RuntimeError(f"{name} is required")
    return value


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=_env("TICKET_DB_HOST", "127.0.0.1"),
        port=int(_env("TICKET_DB_PORT", "3306")),
        user=_env("TICKET_DB_USER", "openrobot_ro"),
        password=_env("TICKET_DB_PASSWORD"),
        database=_env("TICKET_DB_NAME", "helpdesk_724"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
    )


def _authorised(request: Request) -> None:
    expected = _env("TICKET_API_TOKEN")
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix) or header[len(prefix):].strip() != expected:
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/tickets/{ticket_id}")
def ticket(ticket_id: str, request: Request) -> JSONResponse:
    _authorised(request)
    match = TICKET_ID.match(ticket_id.strip())
    if not match:
        raise HTTPException(status_code=400, detail="ticket id must look like ORS-1053")

    number = int(match.group(2))
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, project_id, task_type, updated_at, resolved_at "
                "FROM tasks WHERE id = %s LIMIT 1",
                (number,),
            )
            row = cur.fetchone()
    except pymysql.MySQLError as exc:
        # Never invent a status on failure; CI must treat this as "unknown/blocked".
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc.__class__.__name__}") from exc

    if not row:
        raise HTTPException(status_code=404, detail=f"ticket {number} not found")

    status = str(row.get("status") or "").strip().lower()
    payload: dict[str, Any] = {
        "ticket_id": number,
        "status": status,
        "status_known": status in ALLOWED_STATUSES,
        "project_id": row.get("project_id"),
        "task_type": row.get("task_type"),
        # resolved/closed is the only thing CI gates on; expose it explicitly so the
        # workflow does not have to re-implement the rule.
        "eligible": status in {"resolved", "closed"},
    }
    return JSONResponse(payload)
