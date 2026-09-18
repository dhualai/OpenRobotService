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
    """Filter tickets by project, status, type, tag, and excluded creators."""

    def __init__(
        self,
        source: TicketSource,
        *,
        project_id: str = "Leo_test",
        statuses: Iterable[str] = ("new",),
        task_types: Iterable[str] = ("feature", "bug"),
        tag: str = "auto_case",
        exclude_creators: Iterable[str] = (),
    ) -> None:
        self.source = source
        self.project_id = project_id
        self.statuses = {item.lower() for item in statuses}
        self.task_types = {item.lower() for item in task_types}
        self.tag = tag.lower()
        self.exclude_creators = {item.lower() for item in exclude_creators}

    def scan(self, limit: int = 100) -> list[TicketCandidate]:
        result = []
        for ticket in self.source.fetch(limit):
            if ticket.project_id != self.project_id:
                continue
            if ticket.status.lower() not in self.statuses:
                continue
            if ticket.task_type.lower() not in self.task_types:
                continue
            if self.tag not in {tag.lower() for tag in ticket.tags}:
                continue
            if ticket.created_by.lower() in self.exclude_creators:
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

    def fetch(self, limit: int) -> list[TicketCandidate]:
        local_port = _free_port()
        tunnel = self._start_tunnel(local_port)
        conn = None
        try:
            conn = self._connect(local_port)
            sql = """
                SELECT
                    id,
                    title,
                    description,
                    task_type,
                    status,
                    project_id,
                    project_name,
                    tags,
                    metadata_info,
                    created_by,
                    source,
                    created_at
                FROM tasks
                WHERE project_id = %s
                  AND status = %s
                  AND task_type IN (%s, %s)
                  AND JSON_CONTAINS(COALESCE(tags, JSON_ARRAY()), JSON_QUOTE(%s))
                ORDER BY created_at DESC
                LIMIT %s
            """
            with conn.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        "Leo_test",
                        "NEW",
                        "FEATURE",
                        "BUG",
                        "auto_case",
                        int(limit),
                    ),
                )
                rows = cursor.fetchall()
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
        finally:
            if conn is not None:
                conn.close()
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
