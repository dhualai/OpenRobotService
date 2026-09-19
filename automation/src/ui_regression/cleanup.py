"""Best-effort cleanup for UI regression data."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import allure
import httpx

from .db_cleanup import (
    DatabaseCleanup,
    DatabaseCleanupFilter,
)


@dataclass
class CleanupWarning:
    """One non-fatal cleanup failure."""

    resource: str
    message: str


@dataclass
class CleanupResult:
    """Cleanup outcome that never overrides business test status."""

    deleted: list[str] = field(default_factory=list)
    warnings: list[CleanupWarning] = field(default_factory=list)
    database_deleted_rows: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.warnings


class CleanupManager:
    """Delete test tickets and conversations using authorized test accounts."""

    def __init__(
        self,
        backend_url: str,
        *,
        admin_username: str = "",
        admin_password: str = "",
        u1_username: str = "",
        u1_password: str = "",
        db_cleanup: DatabaseCleanup | None = None,
        client: httpx.Client | None = None,
    ):
        self.client = client or httpx.Client(
            base_url=backend_url.rstrip("/"),
            timeout=30.0,
        )
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.u1_username = u1_username
        self.u1_password = u1_password
        self.db_cleanup = db_cleanup

    def cleanup(
        self,
        *,
        ticket_id: int | None = None,
        conversation_id: int | None = None,
    ) -> CleanupResult:
        result = CleanupResult()

        if ticket_id is not None:
            if not self.admin_username or not self.admin_password:
                result.warnings.append(
                    CleanupWarning("工单", "缺少清理管理员账号，跳过")
                )
            else:
                token = self._login(
                    self.admin_username,
                    self.admin_password,
                    result,
                    "清理管理员",
                )
                if token:
                    api_deleted = self._delete(
                        f"/api/tasks/{ticket_id}",
                        token,
                        "工单",
                        result,
                    )
                    if not api_deleted:
                        self._database_cleanup(ticket_id, result)

        if conversation_id is not None:
            if not self.u1_username or not self.u1_password:
                result.warnings.append(
                    CleanupWarning("会话", "缺少 U1 测试账号，跳过")
                )
            else:
                token = self._login(
                    self.u1_username,
                    self.u1_password,
                    result,
                    "U1",
                )
                if token:
                    self._delete(
                        f"/api/call/conversations/{conversation_id}",
                        token,
                        "会话",
                        result,
                    )

        self._attach(result)
        return result

    def _login(
        self,
        username: str,
        password: str,
        result: CleanupResult,
        label: str,
    ) -> str:
        try:
            response = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            )
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort
            result.warnings.append(CleanupWarning(label, f"登录异常: {exc}"))
            return ""
        if response.status_code != 200:
            result.warnings.append(
                CleanupWarning(label, f"登录失败: HTTP {response.status_code}")
            )
            return ""
        token = response.json().get("access_token")
        if not token:
            result.warnings.append(CleanupWarning(label, "登录响应缺少 access_token"))
            return ""
        return str(token)

    def _database_cleanup(
        self,
        ticket_id: int,
        result: CleanupResult,
    ) -> None:
        if self.db_cleanup is None:
            return
        config = self.db_cleanup.config
        try:
            cleanup_result = self.db_cleanup.cleanup(
                DatabaseCleanupFilter(
                    ticket_id=ticket_id,
                    title_prefix=config.title_prefix,
                    project_id=config.project_id,
                    created_by=config.created_by,
                    max_age_hours=config.max_age_hours,
                ),
                execute=True,
            )
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort
            result.warnings.append(
                CleanupWarning("工单", f"数据库补偿清理异常: {exc}")
            )
            return

        result.database_deleted_rows = cleanup_result.deleted_rows
        if cleanup_result.deleted_rows.get("tasks", 0) > 0:
            result.deleted.append("工单(数据库补偿)")
            result.warnings = [
                warning
                for warning in result.warnings
                if warning.resource != "工单"
            ]
        else:
            result.warnings.append(
                CleanupWarning("工单", "数据库补偿未匹配到可清理工单")
            )

    def _delete(
        self,
        path: str,
        token: str,
        resource: str,
        result: CleanupResult,
    ) -> bool:
        try:
            response = self.client.delete(
                path,
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort
            result.warnings.append(CleanupWarning(resource, f"删除异常: {exc}"))
            return False
        if response.status_code in (200, 204, 404):
            result.deleted.append(resource)
            return True
        result.warnings.append(
            CleanupWarning(resource, f"删除失败: HTTP {response.status_code}")
        )
        return False

    @staticmethod
    def _attach(result: CleanupResult) -> None:
        payload: dict[str, Any] = {
            "deleted": result.deleted,
            "database_deleted_rows": result.database_deleted_rows,
            "warnings": [
                {"resource": item.resource, "message": item.message}
                for item in result.warnings
            ],
        }
        allure.attach(
            json.dumps(payload, ensure_ascii=False, indent=2),
            name="清理结果",
            attachment_type=allure.attachment_type.JSON,
        )
