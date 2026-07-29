"""用户契约（Pydantic）。"""
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, EmailStr

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
    department: Optional[str] = None
    responsibility_modules: Optional[List[str]] = None
    job_level: Optional[int] = 1
    duty_text: Optional[str] = None

class UserUpdate(BaseModel):
    password: Optional[str] = None
    permissions: Optional[List[str]] = None
    name: Optional[str] = None
    status: Optional[str] = None
    external_credentials: Optional[Dict[str, Dict[str, str]]] = None
    avatar_resource_id: Optional[int] = None
    department: Optional[str] = None
    responsibility_modules: Optional[List[str]] = None
    job_level: Optional[int] = None
    duty_text: Optional[str] = None

class UserInDB(UserBase):
    id: str
    hashed_password: str
    permissions: List[str]
    roles: Dict[str, List[str]] = {}
    name: Optional[str] = None
    status: str = "inactive"
    external_credentials: Dict[str, Dict[str, str]] = {}
    department: Optional[str] = None
    responsibility_modules: Optional[List[str]] = None
    job_level: Optional[int] = 1
    duty_text: Optional[str] = None
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
    department: Optional[str] = None
    responsibility_modules: Optional[List[str]] = None
    job_level: Optional[int] = 1
    duty_text: Optional[str] = None
    class Config:
        from_attributes = True

class UserDetail(User):
    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str
