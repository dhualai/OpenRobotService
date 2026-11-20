"""再导出 shim（MIGRATION.md 阶段 1）。真实契约已迁至 `app/schemas/role.py`。"""
from app.schemas.role import (
    RoleBase, RoleCreate, RoleUpdate, Role, RoleAssignment, RoleBatchRemoval,
    RolePermissionBase, RolePermissionCreate, RolePermission,
)

__all__ = [
    "RoleBase", "RoleCreate", "RoleUpdate", "Role", "RoleAssignment", "RoleBatchRemoval",
    "RolePermissionBase", "RolePermissionCreate", "RolePermission",
]
