"""再导出 shim（MIGRATION.md 阶段 1）。真实契约已迁至 `app/schemas/permission.py`。"""
from app.schemas.permission import PermissionBase, PermissionCreate, PermissionUpdate, Permission

__all__ = ["PermissionBase", "PermissionCreate", "PermissionUpdate", "Permission"]
