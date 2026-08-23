"""再导出 shim（MIGRATION.md 阶段 1）。真实契约已迁至 `app/schemas/user.py`。"""
from app.schemas.user import (
    UserBase, UserCreate, UserUpdate, UserInDB, User, UserDetail, UserLogin,
)

__all__ = ["UserBase", "UserCreate", "UserUpdate", "UserInDB", "User", "UserDetail", "UserLogin"]
