"""再导出 shim（MIGRATION.md 阶段 1）。真实契约已迁至 `app/schemas/token.py`。"""
from app.schemas.token import TokenBase, TokenCreate, Token, TokenData, RefreshToken

__all__ = ["TokenBase", "TokenCreate", "Token", "TokenData", "RefreshToken"]
