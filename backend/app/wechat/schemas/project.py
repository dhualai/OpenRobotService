from pydantic import BaseModel
from typing import Dict, List, Optional, Any


class ProjectPermission(BaseModel):
    indicators: List[str]


class ProjectPermissions(BaseModel):
    projectPermissions: Dict[str, ProjectPermission]


class ProjectDataAccess(BaseModel):
    project: str
    tag: Optional[str] = None
    indicator: List[str]


class ProjectDataResponse(BaseModel):
    project_id: str
    authorized_indicators: List[str]
    value: Dict[str, Any]