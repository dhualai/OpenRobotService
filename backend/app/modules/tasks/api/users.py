"""tasks 模块 · 可指派人员列表（业务选人接口）。

与 admin 的用户管理接口（/api/admin/users）刻意解耦：
- admin 接口面向"系统管理"，含 external_credentials 等敏感字段，受 backend:user:* 权限管控；
- 本接口面向"业务选人"（升级/派单/指派），仅需登录即可访问，
  且字段最小化（id/username/name/status），不返回任何凭据，避免敏感信息随管理接口演进而泄露。
"""
from fastapi import APIRouter, Depends, Query, HTTPException, status
from typing import List, Optional, Dict, Any

from pydantic import BaseModel

from app.core.database import db_manager, UserDB
from app.modules.admin.api.auth import get_current_active_user_from_token

router = APIRouter(tags=["tasks"])


class AssignableUser(BaseModel):
    id: str
    username: str
    name: Optional[str] = None
    status: str = "inactive"


@router.get("/assignable-users", response_model=List[AssignableUser], summary="可指派人员列表（升级/派单选人）")
async def list_assignable_users(
    keyword: Optional[str] = Query(None, description="按姓名/账号模糊搜索"),
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(1000, ge=1, le=2000, description="返回的最大记录数"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    db = db_manager.get_db()
    try:
        query = db.query(UserDB)
        if keyword:
            kw = f"%{keyword.strip()}%"
            query = query.filter(
                (UserDB.name.ilike(kw)) | (UserDB.username.ilike(kw))
            )
        records = query.offset(skip).limit(limit).all()
        return [
            AssignableUser(
                id=r.id,
                username=r.username,
                name=getattr(r, "name", None),
                status=getattr(r, "status", "inactive"),
            )
            for r in records
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取可指派人员列表失败: {str(e)}",
        )
    finally:
        db.close()
