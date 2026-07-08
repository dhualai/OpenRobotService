from typing import Dict, List, Optional, Any
from pydantic import BaseModel

class RoleBase(BaseModel):
    name: str

class RoleCreate(RoleBase):
    pass

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