from typing import Dict, List, Optional, Any
from pydantic import BaseModel, EmailStr

class UserBase(BaseModel):
    username: str
    name: Optional[str] = None
    status: Optional[str] = "inactive"

class UserCreate(UserBase):
    user_id: Optional[str] = None
    password: str
    permissions: List[str] = []
    name: Optional[str] = None
    status: Optional[str] = "inactive"
    external_credentials: Optional[Dict[str, Dict[str, str]]] = {}

class UserUpdate(BaseModel):
    password: Optional[str] = None
    permissions: Optional[List[str]] = None
    name: Optional[str] = None
    status: Optional[str] = None
    external_credentials: Optional[Dict[str, Dict[str, str]]] = None

class UserInDB(UserBase):
    id: str
    hashed_password: str
    permissions: List[str]
    roles: Dict[str, List[str]] = {}
    name: Optional[str] = None
    status: str = "inactive"
    external_credentials: Dict[str, Dict[str, str]] = {}
    
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
    class Config:
        from_attributes = True

class UserDetail(User):
    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str