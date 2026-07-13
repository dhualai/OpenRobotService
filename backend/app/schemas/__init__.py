"""底座契约统一导入面（MIGRATION.md 阶段 1）。

认证/RBAC 底座的 Pydantic 契约集中于 `app/schemas/`；提供唯一导入入口
`from app.schemas import User, Token, RoleCreate, ...`。垂直模块（das/fqa/wechat）
契约随其在阶段 3 迁移，暂不纳入。
"""
from app.schemas.user import User, UserBase, UserCreate, UserUpdate, UserDetail, UserLogin, UserInDB
from app.schemas.token import Token, TokenBase, TokenCreate, RefreshToken, TokenData
from app.schemas.role import (
    Role, RoleBase, RoleCreate, RoleAssignment, RoleBatchRemoval,
    RolePermission, RolePermissionBase, RolePermissionCreate,
)
from app.schemas.project import Project, ProjectBase, ProjectCreate, ProjectUpdate, ProjectUserRoleAssignment
from app.schemas.permission import Permission, PermissionBase, PermissionCreate, PermissionUpdate
from app.schemas.response import BaseResponse, SuccessResponse, DataResponse, ErrorResponse, ListResponse

__all__ = [
    "User", "UserBase", "UserCreate", "UserUpdate", "UserDetail", "UserLogin", "UserInDB",
    "Token", "TokenBase", "TokenCreate", "RefreshToken", "TokenData",
    "Role", "RoleBase", "RoleCreate", "RoleAssignment", "RoleBatchRemoval",
    "RolePermission", "RolePermissionBase", "RolePermissionCreate",
    "Project", "ProjectBase", "ProjectCreate", "ProjectUpdate", "ProjectUserRoleAssignment",
    "Permission", "PermissionBase", "PermissionCreate", "PermissionUpdate",
    "BaseResponse", "SuccessResponse", "DataResponse", "ErrorResponse", "ListResponse",
]
