from pydantic import BaseModel


class WechatUser(BaseModel):
    open_id: str
    username: str
    full_name: str = None
    avatar_url: str = None


class AuthToken(BaseModel):
    access_token: str
    expires_at: int