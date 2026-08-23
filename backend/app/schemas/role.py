"""角色契约（Pydantic）。原 `app/modules/aas/schemas/role.py`，迁入底座（MIGRATION.md 阶段 1）。"""
from typing import Dict, List, Literal, Optional, Any
from pydantic import BaseModel

class RoleBase(BaseModel):
    name: str
    role_type: Literal['system', 'project'] = 'project'

class RoleCreate(RoleBase):
    pass

class RoleUpdate(BaseModel):
    name: Optional[str] = None
    role_type: Optional[Literal['system', 'project']] = None

class Role(RoleBase):
    id: str

    class Config:
        from_attributes = True

class RoleAssignment(BaseModel):
    project_id: str
    role_ids: List[str]
    report_to_id: Optional[str] = None

class RoleBatchRemoval(BaseModel):
    project_id: str
    role_ids: List[str]

class RolePermissionBase(BaseModel):
    role_id: str
    indicator_id: str
    permission_type: str = "read"

class RolePermissionCreate(RolePermissionBase):
    pass

class RolePermission(RolePermissionBase):
    id: str

    class Config:
        from_attributes = True
