#!/usr/bin/env python3
"""Clean real-environment AUTO-* test tickets and related rows.

This is a temporary safety valve while the product admin delete endpoint is
not able to hard-delete tickets with related records.

Safety rules:
- Dry-run by default.
- Only accepts a title prefix starting with ``AUTO-``.
- Deletes only the matched task IDs and their known child rows.

Required environment:
    REAL_DB_HOST      default: 127.0.0.1
    REAL_DB_PORT      default: 3306
    REAL_DB_USER      default: root
    REAL_DB_PASSWORD  required
    REAL_DB_NAME      default: helpdesk_test

Typical local use with an SSH tunnel:
    ssh -N -L 19402:127.0.0.1:3306 usp-a@125.122.97.107 -p 8802
    $env:REAL_DB_PORT = "19402"
    $env:REAL_DB_PASSWORD = "<db-password>"
    python automation/scripts/cli-cleanup-real-test-data.py --dry-run
    python automation/scripts/cli-cleanup-real-test-data.py --execute
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pymysql


CHILD_TABLES = (
    "task_comment_read_record",
    "task_comments",
    "task_dispatch_log",
    "task_operation_logs",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean AUTO-* test tickets from the real test database.",
    )
    parser.add_argument(
        "--prefix",
        default="AUTO-LIFECYCLE-%",
        help="Ticket title LIKE pattern. Must start with AUTO-. Default: %(default)s",
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=0,
        help="Only clean tickets created within N hours. 0 means no time limit.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly preview only. This is also the default behavior.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete rows. Without this flag the script only prints a preview.",
    )
    return parser


def _connect():
    password = os.getenv("REAL_DB_PASSWORD", "")
    if not password:
        print("ERROR: REAL_DB_PASSWORD is required", file=sys.stderr)
        raise SystemExit(2)

    return pymysql.connect(
        host=os.getenv("REAL_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("REAL_DB_PORT", "3306")),
        user=os.getenv("REAL_DB_USER", "root"),
        password=password,
        database=os.getenv("REAL_DB_NAME", "helpdesk_test"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _select_tasks(conn, prefix: str, max_age_hours: int) -> list[dict]:
    sql = "SELECT id, title, status, created_at FROM tasks WHERE title LIKE %s"
    params: list = [prefix]
    if max_age_hours > 0:
        sql += " AND created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)"
        params.append(max_age_hours)
    sql += " ORDER BY id DESC"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def _delete_tasks(conn, task_ids: list[int], prefix: str) -> dict[str, int]:
    placeholders = ", ".join(["%s"] * len(task_ids))
    deleted: dict[str, int] = {}
    with conn.cursor() as cursor:
        for table in CHILD_TABLES:
            cursor.execute(
                f"DELETE FROM {table} WHERE task_id IN ({placeholders})",
                task_ids,
            )
            deleted[table] = cursor.rowcount
        cursor.execute(
            f"DELETE FROM tasks WHERE id IN ({placeholders}) AND title LIKE %s",
            [*task_ids, prefix],
        )
        deleted["tasks"] = cursor.rowcount
    conn.commit()
    return deleted


def main() -> int:
    args = _build_parser().parse_args()
    if not args.prefix.startswith("AUTO-"):
        print("ERROR: --prefix must start with AUTO-", file=sys.stderr)
        return 2

    conn = _connect()
    try:
        tasks = _select_tasks(conn, args.prefix, args.max_age_hours)
        if not tasks:
            print(f"No matching test tickets found. prefix={args.prefix!r}")
            return 0

        print(f"Matched {len(tasks)} test ticket(s):")
        for task in tasks:
            print(
                f"  id={task['id']} status={task['status']} "
                f"created_at={task['created_at']} title={task['title']}"
            )

        if not args.execute:
            print("Dry-run only. Re-run with --execute to delete these rows.")
            return 0

        deleted = _delete_tasks(conn, [int(task["id"]) for task in tasks], args.prefix)
        print("Deleted rows:")
        for table, count in deleted.items():
            print(f"  {table}: {count}")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: cleanup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
