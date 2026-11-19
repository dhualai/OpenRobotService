#!/usr/bin/env python3
"""Generate install secrets on the target machine. Never commit the output."""
from __future__ import annotations

import argparse
import secrets
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", type=Path, default=None, help="write chmod 600 env file")
    ap.add_argument(
        "--db-host",
        default="127.0.0.1",
        help="Postgres hostname in DATABASE_URL (compose service is 'db')",
    )
    ap.add_argument(
        "--ors-base-url",
        default="http://127.0.0.1:9400",
        help="摇人吧后端地址（账号统一校验用）",
    )
    args = ap.parse_args()
    pg = secrets.token_urlsafe(24)
    session = secrets.token_urlsafe(32)
    content = secrets.token_urlsafe(32)
    body = (
        f"POSTGRES_USER=topo\n"
        f"POSTGRES_PASSWORD={pg}\n"
        f"POSTGRES_DB=topo\n"
        f"DATABASE_URL=postgresql+psycopg://topo:{pg}@{args.db_host}:5432/topo\n"
        f"SESSION_SECRET={session}\n"
        f"TOPO_CONTENT_KEY={content}\n"
        f"ORS_BASE_URL={args.ors_base_url}\n"
        f"DEBUG=false\n"
        f"OPENAPI_ENABLED=false\n"
        f"USP_TEST_BASE_URL=http://10.10.10.202:9200\n"
        f"USP_TEST_ADMIN_LICENSE=\n"
    )
    if args.write:
        path = args.write
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)
        print(f"wrote {path} (mode 600). 账号统一走摇人吧，无本地管理员口令。")
        return
    print(body, end="")


if __name__ == "__main__":
    main()
