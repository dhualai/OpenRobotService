"""Read-only scanner for production tickets that need test-case generation."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from typing import Iterable, Protocol

import pymysql

from automation.src.ticket_pipeline.models import TicketCandidate


class TicketSource(Protocol):
    """Read-only source of candidate tickets."""

    def fetch(self, limit: int) -> list[TicketCandidate]:
        ...


class TicketScanner:
    """Filter tickets by project, status, type, tag, creator and source."""

    def __init__(
        self,
        source: TicketSource,
        *,
        project_id: str = "Leo_test",
        statuses: Iterable[str] = ("new",),
        task_types: Iterable[str] = ("feature", "bug"),
        tag: str = "",
        exclude_creators: Iterable[str] = (),
        exclude_sources: Iterable[str] = ("ai",),
    ) -> None:
        self.source = source
        self.project_id = project_id
        self.statuses = {item.lower() for item in statuses}
        self.task_types = {item.lower() for item in task_types}
        self.tag = tag.lower() if tag else ""
        self.exclude_creators = {item.lower() for item in exclude_creators}
        self.exclude_sources = {item.lower() for item in exclude_sources if item}

    def scan(self, limit: int = 100) -> list[TicketCandidate]:
        result = []
        for ticket in self.source.fetch(limit):
            if ticket.project_id != self.project_id:
                continue
            if ticket.status.lower() not in self.statuses:
                continue
            if ticket.task_type.lower() not in self.task_types:
                continue
            if self.tag and self.tag not in {tag.lower() for tag in ticket.tags}:
                continue
            if ticket.created_by.lower() in self.exclude_creators:
                continue
            if (ticket.source or "").lower() in self.exclude_sources:
                continue
            result.append(ticket)
        return result


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _load_json(value):
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


class SSHMySQLTicketSource:
    """Read production tickets through a temporary SSH tunnel and MySQL.

    The scanner never writes to the source database. It starts a short-lived
    SSH local port forward, runs a parameterized SELECT, and then closes it.
    """

    def __init__(
        self,
        *,
        ssh_host: str = "125.122.97.107",
        ssh_port: int = 8802,
        ssh_user: str = "usp-a",
        ssh_key: str = "",
        db_host: str = "127.0.0.1",
        db_port: int = 3306,
        db_user: str = "root",
        db_password: str = "",
        db_name: str = "helpdesk_724",
        connect_timeout: int = 10,
        project_id: str = "Leo_test",
        statuses: Iterable[str] = ("new",),
        task_types: Iterable[str] = ("feature", "bug"),
        tag: str = "",
        exclude_sources: Iterable[str] = ("ai",),
    ) -> None:
        if not db_password:
            raise ValueError("PROD_TICKET_DB_PASSWORD is required")
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.db_host = db_host
        self.db_port = db_port
        self.db_user = db_user
        self.db_password = db_password
        self.db_name = db_name
        self.connect_timeout = connect_timeout
        self.project_id = project_id
        self.statuses = tuple(item.strip().lower() for item in statuses if item.strip())
        self.task_types = tuple(item.strip().lower() for item in task_types if item.strip())
        self.tag = (tag or "").strip().lower()
        self.exclude_sources = tuple(
            item.strip().lower() for item in exclude_sources if item and item.strip()
        )

    @classmethod
    def from_env(cls) -> "SSHMySQLTicketSource":
        return cls(
            ssh_host=os.getenv("PROD_TICKET_SSH_HOST", "125.122.97.107"),
            ssh_port=int(os.getenv("PROD_TICKET_SSH_PORT", "8802")),
            ssh_user=os.getenv("PROD_TICKET_SSH_USER", "usp-a"),
            ssh_key=os.getenv("PROD_TICKET_SSH_KEY", ""),
            db_host=os.getenv("PROD_TICKET_DB_HOST", "127.0.0.1"),
            db_port=int(os.getenv("PROD_TICKET_DB_PORT", "3306")),
            db_user=os.getenv("PROD_TICKET_DB_USER", "root"),
            db_password=os.getenv("PROD_TICKET_DB_PASSWORD", ""),
            db_name=os.getenv("PROD_TICKET_DB_NAME", "helpdesk_724"),
            connect_timeout=int(os.getenv("PROD_TICKET_CONNECT_TIMEOUT", "10")),
            project_id=os.getenv("PROD_TICKET_PROJECT_ID", "Leo_test"),
            statuses=tuple(
                item
                for item in os.getenv("PROD_TICKET_STATUSES", "new").split(",")
                if item.strip()
            ),
            task_types=tuple(
                item
                for item in os.getenv("PROD_TICKET_TASK_TYPES", "feature,bug").split(",")
                if item.strip()
            ),
            tag=os.getenv("PROD_TICKET_TAG", ""),
            exclude_sources=tuple(
                item
                for item in os.getenv("PROD_TICKET_EXCLUDE_SOURCES", "ai").split(",")
                if item.strip()
            ),
        )

    def _start_tunnel(self, local_port: int) -> subprocess.Popen:
        cmd = [
            "ssh",
            "-N",
            "-p",
            str(self.ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-L",
            f"{local_port}:{self.db_host}:{self.db_port}",
        ]
        if self.ssh_key:
            cmd.extend(["-i", self.ssh_key])
        cmd.append(f"{self.ssh_user}@{self.ssh_host}")
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _connect(self, local_port: int):
        deadline = time.monotonic() + self.connect_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return pymysql.connect(
                    host="127.0.0.1",
                    port=local_port,
                    user=self.db_user,
                    password=self.db_password,
                    database=self.db_name,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True,
                    connect_timeout=3,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"Could not connect to production MySQL through tunnel: {last_error}")

    _COLUMNS = (
        "id, title, description, task_type, status, project_id, project_name, "
        "tags, metadata_info, created_by, source, created_at, updated_at, resolved_at"
    )

    def _scan_clause(self) -> tuple[str, list]:
        """Build the WHERE clause for a scan query with bound parameters."""
        clauses = ["project_id = %s"]
        params: list = [self.project_id]
        if self.statuses:
            clauses.append("status IN (" + ", ".join(["%s"] * len(self.statuses)) + ")")
            params.extend(item.upper() for item in self.statuses)
        if self.task_types:
            clauses.append("task_type IN (" + ", ".join(["%s"] * len(self.task_types)) + ")")
            params.extend(item.upper() for item in self.task_types)
        if self.tag:
            clauses.append("JSON_CONTAINS(COALESCE(tags, JSON_ARRAY()), JSON_QUOTE(%s))")
            params.append(self.tag)
        if self.exclude_sources:
            clauses.append(
                "COALESCE(source, 'manual') NOT IN ("
                + ", ".join(["%s"] * len(self.exclude_sources))
                + ")"
            )
            params.extend(self.exclude_sources)
        return " AND ".join(clauses), params

    def _rows_to_candidates(self, rows) -> list[TicketCandidate]:
        return [
            TicketCandidate(
                **{
                    **row,
                    "task_type": row["task_type"].lower(),
                    "status": row["status"].lower(),
                    "tags": _load_json(row.get("tags")) or [],
                    "metadata_info": _load_json(row.get("metadata_info")) or {},
                }
            )
            for row in rows
        ]

    def _query(self, sql: str, params: tuple) -> list[TicketCandidate]:
        local_port = _free_port()
        tunnel = self._start_tunnel(local_port)
        conn = None
        try:
            conn = self._connect(local_port)
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            return self._rows_to_candidates(rows)
        finally:
            if conn is not None:
                conn.close()
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()

    def fetch(self, limit: int) -> list[TicketCandidate]:
        where, params = self._scan_clause()
        sql = f"""
            SELECT {self._COLUMNS}
            FROM tasks
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT %s
        """
        return self._query(sql, (*params, int(limit)))

    def fetch_by_ids(self, ticket_ids: Iterable[int]) -> list[TicketCandidate]:
        """Fetch specific tickets by id, regardless of scan filters."""
        ids = [int(item) for item in ticket_ids]
        if not ids:
            return []
        sql = f"""
            SELECT {self._COLUMNS}
            FROM tasks
            WHERE id IN ({", ".join(["%s"] * len(ids))})
        """
        return self._query(sql, tuple(ids))
