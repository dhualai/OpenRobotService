from typing import Optional, List, Dict
from pydantic import BaseModel

class ProjectBase(BaseModel):
    name: str

class ProjectCreate(ProjectBase):
    name: Optional[str] = None
    code: str

class ProjectUpdate(ProjectBase):
    pass

class Project(ProjectBase):
    id: str
    
    class Config:
        from_attributes = True

class ProjectUserRoleAssignment(BaseModel):
    project_id: str
    organization_ids: List[Dict[str, str]]