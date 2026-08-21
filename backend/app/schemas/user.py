"""用户契约（Pydantic）。"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, EmailStr

# 责任模块：目标三层结构 {产品: {界面: [功能]} }（与 module_trees 责任树口径一致）。
# 迁移期兼容旧两层 {产品: [模块]} 与旧扁平列表；故类型用宽松 Dict[str, Any]，
# 避免 Pydantic 对嵌套值严格校验而拒绝历史数据。
ResponsibilityModules = Dict[str, Any]

class UserBase(BaseModel):
    username: str
    name: Optional[str] = None
    status: Optional[str] = "inactive"
    avatar_resource_id: Optional[int] = None

class UserCreate(UserBase):
    user_id: Optional[str] = None
    password: str
    permissions: List[str] = []
    name: Optional[str] = None
    status: Optional[str] = "inactive"
    external_credentials: Optional[Dict[str, Dict[str, str]]] = {}
    company: Optional[str] = None
    department: Optional[str] = None
    company_id: Optional[str] = None
    department_id: Optional[str] = None
    responsibility_modules: Optional[ResponsibilityModules] = None
    job_level: Optional[int] = 1
    duty_text: Optional[str] = None
    supervisor_id: Optional[str] = None

class UserUpdate(BaseModel):
    password: Optional[str] = None
    permissions: Optional[List[str]] = None
    name: Optional[str] = None
    status: Optional[str] = None
    external_credentials: Optional[Dict[str, Dict[str, str]]] = None
    avatar_resource_id: Optional[int] = None
    company: Optional[str] = None
    department: Optional[str] = None
    company_id: Optional[str] = None
    department_id: Optional[str] = None
    responsibility_modules: Optional[ResponsibilityModules] = None
    job_level: Optional[int] = None
    duty_text: Optional[str] = None
    supervisor_id: Optional[str] = None

class UserInDB(UserBase):
    id: str
    hashed_password: str
    permissions: List[str]
    roles: Dict[str, List[str]] = {}
    name: Optional[str] = None
    status: str = "inactive"
    external_credentials: Dict[str, Dict[str, str]] = {}
    company: Optional[str] = None
    department: Optional[str] = None
    company_id: Optional[str] = None
    department_id: Optional[str] = None
    responsibility_modules: Optional[ResponsibilityModules] = None
    job_level: Optional[int] = 1
    duty_text: Optional[str] = None
    supervisor_id: Optional[str] = None
    class Config:
        from_attributes = True

class User(UserBase):
    id: str
    permissions: List[str]
    roles: Dict[str, List[str]] = {}
    projectPermissions: Dict[str, Dict[str, List[str]]] = {}
    name: Optional[str] = None
    status: str = "inactive"
    external_credentials: Dict[str, Dict[str, str]] = {}
    company: Optional[str] = None
    department: Optional[str] = None
    company_id: Optional[str] = None
    department_id: Optional[str] = None
    responsibility_modules: Optional[ResponsibilityModules] = None
    job_level: Optional[int] = 1
    duty_text: Optional[str] = None
    supervisor_id: Optional[str] = None
    # 用户在项目中的角色关系（含汇报人 report_to_id），用于前端构建汇报树
    project_role_relations: List[Dict[str, Any]] = []
    class Config:
        from_attributes = True

class UserDetail(User):
    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str
