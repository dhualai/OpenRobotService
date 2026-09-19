#!/usr/bin/env python3
"""Compensation cleanup for real-environment UI regression tickets.

Safety rules:
- Dry-run by default.
- Requires project_id.
- Accepts either exact ticket IDs or a title prefix.
- Refuses non-test database names.
- Deletes all known task child rows in one transaction.

Environment:
    REAL_DB_HOST / UI_REGRESSION_DB_HOST
    REAL_DB_PORT / UI_REGRESSION_DB_PORT
    REAL_DB_USER / UI_REGRESSION_DB_USER
    REAL_DB_PASSWORD / UI_REGRESSION_DB_PASSWORD
    REAL_DB_NAME / UI_REGRESSION_DB_NAME
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.src.ui_regression.db_cleanup import (
    DatabaseCleanup,
    DatabaseCleanupConfig,
    DatabaseCleanupFilter,
)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean UI regression tickets from the real test database.",
    )
    parser.add_argument(
        "--ticket-id",
        type=int,
        action="append",
        default=[],
        help="Exact task ID. Can be repeated.",
    )
    parser.add_argument(
        "--prefix",
        default=_env(
            "UI_REGRESSION_CLEANUP_TITLE_PREFIX",
            default="自动化链路验证-%",
        ),
        help="Ticket title LIKE pattern. Default: %(default)s",
    )
    parser.add_argument(
        "--project-id",
        default=_env(
            "UI_REGRESSION_CLEANUP_PROJECT_ID",
            default="Leo_test",
        ),
        help="Required project ID filter. Default: %(default)s",
    )
    parser.add_argument(
        "--created-by",
        default=_env("UI_REGRESSION_CLEANUP_CREATED_BY"),
        help="Optional tasks.created_by exact filter.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=int(
            _env("UI_REGRESSION_CLEANUP_MAX_AGE_HOURS", default="0")
        ),
        help="Only clean tickets created within N hours. 0 means no limit.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly preview only. This is also the default.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete rows.",
    )
    return parser


def _load_config() -> DatabaseCleanupConfig:
    return DatabaseCleanupConfig(
        host=_env(
            "REAL_DB_HOST",
            "UI_REGRESSION_DB_HOST",
            default="127.0.0.1",
        ),
        port=int(
            _env(
                "REAL_DB_PORT",
                "UI_REGRESSION_DB_PORT",
                default="3306",
            )
        ),
        user=_env(
            "REAL_DB_USER",
            "UI_REGRESSION_DB_USER",
            default="automation_cleanup",
        ),
        password=_env("REAL_DB_PASSWORD", "UI_REGRESSION_DB_PASSWORD"),
        database=_env(
            "REAL_DB_NAME",
            "UI_REGRESSION_DB_NAME",
            default="helpdesk_test",
        ),
        title_prefix=_env(
            "UI_REGRESSION_CLEANUP_TITLE_PREFIX",
            default="自动化链路验证-%",
        ),
        project_id=_env(
            "UI_REGRESSION_CLEANUP_PROJECT_ID",
            default="Leo_test",
        ),
        created_by=_env("UI_REGRESSION_CLEANUP_CREATED_BY"),
        max_age_hours=int(
            _env("UI_REGRESSION_CLEANUP_MAX_AGE_HOURS", default="0")
        ),
    )


def _print_result(result, *, execute: bool) -> None:
    print(f"Matched {result.matched_count} test ticket(s):")
    for task in result.matched_tasks:
        print(
            f"  id={task['id']} status={task['status']} "
            f"project_id={task['project_id']} "
            f"created_by={task['created_by']} "
            f"created_at={task['created_at']} title={task['title']}"
        )
    if not execute:
        print("Dry-run only. Re-run with --execute to delete these rows.")
        return
    print("Deleted rows:")
    for table, count in result.deleted_rows.items():
        print(f"  {table}: {count}")


def main() -> int:
    args = _build_parser().parse_args()
    cleaner = DatabaseCleanup(_load_config())
    filters: list[DatabaseCleanupFilter] = []

    if args.ticket_id:
        filters.extend(
            DatabaseCleanupFilter(
                ticket_id=ticket_id,
                title_prefix=args.prefix,
                project_id=args.project_id,
                created_by=args.created_by,
                max_age_hours=args.max_age_hours,
            )
            for ticket_id in args.ticket_id
        )
    else:
        filters.append(
            DatabaseCleanupFilter(
                title_prefix=args.prefix,
                project_id=args.project_id,
                created_by=args.created_by,
                max_age_hours=args.max_age_hours,
            )
        )

    try:
        for cleanup_filter in filters:
            result = cleaner.cleanup(
                cleanup_filter,
                execute=args.execute,
            )
            _print_result(result, execute=args.execute)
        return 0
    except Exception as exc:
        print(f"ERROR: cleanup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
