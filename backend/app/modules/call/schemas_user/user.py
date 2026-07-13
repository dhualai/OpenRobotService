from pydantic import BaseModel
from typing import List, Dict, Optional, Any


class UserLoginRequest(BaseModel):
    username: str
    password: str


class UserLoginResponse(BaseModel):
    token_type: str
    access_token: str
    expires_in: int
    refresh_token: Optional[str] = None
    permissions: List[str] = []
    is_admin: bool = False


class RefreshToken(BaseModel):
    refresh_token: str


class Token(BaseModel):
    refresh_token: Optional[str] = None


class UserInfoResponse(BaseModel):
    username: str
    name: Optional[str] = None
    status: str
    id: str
    permissions: List[str]
    roles: Dict[str, List[str]]
    projectPermissions: Dict[str, Dict[str, List[str]]]


class TokenPayload(BaseModel):
    sub: str
    exp: int
    type: Optional[str] = None
    permissions: List[str] = []
    is_admin: bool = False