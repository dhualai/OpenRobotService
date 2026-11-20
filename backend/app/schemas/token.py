"""令牌契约（Pydantic）。原 `app/modules/aas/schemas/token.py`，迁入底座（MIGRATION.md 阶段 1）。"""
from typing import Optional
from pydantic import BaseModel

class TokenBase(BaseModel):
    token_type: str = "bearer"

class TokenCreate(TokenBase):
    access_token: str
    expires_in: Optional[int] = None

class Token(TokenCreate):
    refresh_token: Optional[str] = None

class TokenData(BaseModel):
    username: Optional[str] = None

class RefreshToken(BaseModel):
    refresh_token: str
