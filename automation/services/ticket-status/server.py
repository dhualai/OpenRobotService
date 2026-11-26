#!/usr/bin/env python3
"""工单状态只读接口（单文件，零第三方依赖）。

跑在 MySQL 所在的那台云服务器上，CI 用 curl 查一个工单的状态。
依赖只有 python3 和 mysql 客户端——不装包、不建虚拟环境、不需要反向代理也能先用起来。

用法：
    TICKET_API_TOKEN=xxx TICKET_DB_PASSWORD=yyy python3 server.py
    # 默认监听 0.0.0.0:8099，可用 TICKET_API_BIND / TICKET_API_PORT 改

接口：
    GET /health
    GET /tickets/1053   （需要 Authorization: Bearer <token>）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("TICKET_API_TOKEN", "")
DB_NAME = os.environ.get("TICKET_DB_NAME", "helpdesk_724")
DB_USER = os.environ.get("TICKET_DB_USER", "openrobot_ro")
DB_PASSWORD = os.environ.get("TICKET_DB_PASSWORD", "")
DB_HOST = os.environ.get("TICKET_DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("TICKET_DB_PORT", "3306")
# 默认只监听回环地址：服务本身不带 TLS，直接绑 0.0.0.0 等于把带凭据的接口
# 明文暴露到公网。要对外提供访问，请在前面加 HTTPS 反代，或显式设置
# TICKET_API_ALLOW_PUBLIC=1 并自行承担网络层防护。
BIND = os.environ.get("TICKET_API_BIND", "127.0.0.1")
ALLOW_PUBLIC = os.environ.get("TICKET_API_ALLOW_PUBLIC", "") == "1"
PORT = int(os.environ.get("TICKET_API_PORT", "8099"))

TICKET_ID = re.compile(r"^(?:ORS-)?(\d{1,10})$", re.IGNORECASE)


def query_ticket(number: int) -> dict | None:
    """只读一条工单。返回 None 表示不存在，抛异常表示查询失败。"""
    sql = (
        f"SELECT id, status, project_id, task_type FROM {DB_NAME}.tasks "
        f"WHERE id = {number} LIMIT 1"
    )
    env = dict(os.environ, MYSQL_PWD=DB_PASSWORD)  # 口令不进命令行，避免 ps 可见
    proc = subprocess.run(
        ["mysql", "-N", "-B", f"-h{DB_HOST}", f"-P{DB_PORT}", f"-u{DB_USER}", "-e", sql],
        capture_output=True, text=True, timeout=10, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:200] or "mysql failed")

    rows = [line for line in proc.stdout.splitlines() if line.strip()]
    if not rows:
        return None
    cells = rows[0].split("\t")
    status = (cells[1] if len(cells) > 1 else "").strip().lower()
    return {
        "ticket_id": number,
        "status": status,
        "eligible": status in {"resolved", "closed"},
        "project_id": cells[2] if len(cells) > 2 else "",
        "task_type": cells[3] if len(cells) > 3 else "",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ticket-status/1.0"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        if self.path.rstrip("/") == "/health":
            self._send(200, {"status": "ok"})
            return

        match = re.fullmatch(r"/tickets/([^/]+)/?", self.path)
        if not match:
            self._send(404, {"detail": "not found"})
            return

        if self.headers.get("Authorization", "") != f"Bearer {TOKEN}" or not TOKEN:
            self._send(401, {"detail": "invalid token"})
            return

        ticket = TICKET_ID.match(match.group(1))
        if not ticket:
            self._send(400, {"detail": "ticket id must look like ORS-1053"})
            return

        try:
            row = query_ticket(int(ticket.group(1)))
        except Exception as exc:  # noqa: BLE001 - 任何失败都必须是 503，不能编状态
            self._send(503, {"detail": f"lookup failed: {exc}"})
            return
        if row is None:
            self._send(404, {"detail": "ticket not found"})
            return
        self._send(200, row)

    def do_POST(self) -> None:  # noqa: N802
        self._send(405, {"detail": "read-only"})

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    if not TOKEN or not DB_PASSWORD:
        raise SystemExit("TICKET_API_TOKEN 和 TICKET_DB_PASSWORD 必须设置")
    if BIND in ("0.0.0.0", "::") and not ALLOW_PUBLIC:
        raise SystemExit(
            "拒绝启动：默认只监听 127.0.0.1。\n"
            "要对外暴露，请先加 HTTPS 反向代理；确有需要时设置 TICKET_API_ALLOW_PUBLIC=1。"
        )
    print(f"ticket-status listening on {BIND}:{PORT}", flush=True)
    HTTPServer((BIND, PORT), Handler).serve_forever()
