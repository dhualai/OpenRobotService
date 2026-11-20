"""Strict database compensation cleanup for UI regression tickets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pymysql


CHILD_TABLES = (
    "task_comment_read_record",
    "task_comments",
    "task_comment_read",
    "task_dispatch_log",
    "task_operation_logs",
    "task_followers",
    "task_participants",
    "task_spec_doc",
)


def _is_safe_test_database(database: str) -> bool:
    normalized = database.strip().lower()
    return normalized == "helpdesk_test" or normalized.endswith("_test")


@dataclass(frozen=True)
class DatabaseCleanupConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    title_prefix: str = "自动化链路验证-%"
    project_id: str = "Leo_test"
    created_by: str = ""
    max_age_hours: int = 0

    def __post_init__(self) -> None:
        if not _is_safe_test_database(self.database):
            raise ValueError(f"Refusing unsafe database: {self.database}")
        if not self.password:
            raise ValueError("Database cleanup password is required")
        if not self.title_prefix:
            raise ValueError("Database cleanup title prefix is required")
        if not self.project_id:
            raise ValueError("Database cleanup project_id is required")

    @classmethod
    def from_env(
        cls,
        *,
        port_override: int | None = None,
    ) -> "DatabaseCleanupConfig | None":
        enabled = os.getenv("UI_REGRESSION_DB_CLEANUP_ENABLED", "0") == "1"
        if not enabled:
            return None
        return cls(
            host=os.getenv("UI_REGRESSION_DB_HOST", "127.0.0.1"),
            port=port_override
            or int(os.getenv("UI_REGRESSION_DB_PORT", "19402")),
            user=os.getenv("UI_REGRESSION_DB_USER", "automation_cleanup"),
            password=os.getenv("UI_REGRESSION_DB_PASSWORD", ""),
            database=os.getenv("UI_REGRESSION_DB_NAME", "helpdesk_test"),
            title_prefix=os.getenv(
                "UI_REGRESSION_CLEANUP_TITLE_PREFIX",
                "自动化链路验证-%",
            ),
            project_id=os.getenv(
                "UI_REGRESSION_CLEANUP_PROJECT_ID",
                "Leo_test",
            ),
            created_by=os.getenv("UI_REGRESSION_CLEANUP_CREATED_BY", ""),
            max_age_hours=int(
                os.getenv("UI_REGRESSION_CLEANUP_MAX_AGE_HOURS", "0")
            ),
        )


@dataclass(frozen=True)
class DatabaseCleanupFilter:
    ticket_id: int | None = None
    title_prefix: str = ""
    project_id: str = ""
    created_by: str = ""
    max_age_hours: int = 0

    def validate(self) -> None:
        if self.ticket_id is None and not self.title_prefix:
            raise ValueError("ticket_id or title_prefix is required")
        if not self.project_id:
            raise ValueError("project_id is required")


@dataclass
class DatabaseCleanupResult:
    matched_tasks: list[dict] = field(default_factory=list)
    deleted_rows: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return len(self.matched_tasks)


def build_task_conditions(
    cleanup_filter: DatabaseCleanupFilter,
) -> tuple[str, list]:
    cleanup_filter.validate()
    conditions = ["project_id = %s"]
    params: list = [cleanup_filter.project_id]

    if cleanup_filter.ticket_id is not None:
        conditions.append("id = %s")
        params.append(cleanup_filter.ticket_id)
    if cleanup_filter.title_prefix:
        conditions.append("title LIKE %s")
        params.append(cleanup_filter.title_prefix)
    if cleanup_filter.created_by:
        conditions.append("created_by = %s")
        params.append(cleanup_filter.created_by)
    if cleanup_filter.max_age_hours > 0:
        conditions.append("created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)")
        params.append(cleanup_filter.max_age_hours)

    return " AND ".join(conditions), params


class DatabaseCleanup:
    """Delete test tickets and all known child rows in one transaction."""

    def __init__(self, config: DatabaseCleanupConfig):
        self.config = config

    def cleanup(
        self,
        cleanup_filter: DatabaseCleanupFilter,
        *,
        execute: bool = False,
    ) -> DatabaseCleanupResult:
        cleanup_filter.validate()
        connection = self._connect()
        try:
            tasks = self._select_tasks(connection, cleanup_filter, execute)
            result = DatabaseCleanupResult(matched_tasks=tasks)
            if execute and tasks:
                task_ids = [int(task["id"]) for task in tasks]
                result.deleted_rows = self._delete_tasks(
                    connection,
                    task_ids,
                    cleanup_filter,
                )
                connection.commit()
            else:
                connection.rollback()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self):
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    @staticmethod
    def _select_tasks(
        connection,
        cleanup_filter: DatabaseCleanupFilter,
        execute: bool,
    ) -> list[dict]:
        where_sql, params = build_task_conditions(cleanup_filter)
        sql = (
            "SELECT id, title, status, project_id, created_by, created_at "
            f"FROM tasks WHERE {where_sql} ORDER BY id DESC"
        )
        if execute:
            sql += " FOR UPDATE"
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    @staticmethod
    def _delete_tasks(
        connection,
        task_ids: list[int],
        cleanup_filter: DatabaseCleanupFilter,
    ) -> dict[str, int]:
        placeholders = ", ".join(["%s"] * len(task_ids))
        deleted: dict[str, int] = {}
        with connection.cursor() as cursor:
            for table in CHILD_TABLES:
                cursor.execute(
                    f"DELETE FROM {table} WHERE task_id IN ({placeholders})",
                    task_ids,
                )
                deleted[table] = cursor.rowcount

            where_sql, filter_params = build_task_conditions(cleanup_filter)
            cursor.execute(
                "DELETE FROM tasks "
                f"WHERE id IN ({placeholders}) AND {where_sql}",
                [*task_ids, *filter_params],
            )
            deleted["tasks"] = cursor.rowcount
        return deleted
