"""权限契约（Pydantic）。原 `app/modules/aas/schemas/permission.py`，迁入底座（MIGRATION.md 阶段 1）。"""
from pydantic import BaseModel
from typing import Optional

class PermissionBase(BaseModel):
    code: str
    name: str
    resource_type: str
    action: str
    description: Optional[str] = None

class PermissionCreate(PermissionBase):
    pass

class PermissionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class Permission(PermissionBase):
    id: str

    class Config:
        from_attributes = True
