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
    # ── 派单画像（供重派「全部用户」分组展示；无画像字段为空）──
    department: Optional[str] = None
    job_level: Optional[int] = None
    modules: Optional[List[str]] = None
    duty: Optional[str] = None
    # 是否有职责画像（department 或 responsibility_modules 或 duty_text 任一非空）
    has_profile: bool = False


def _modules_to_flat(modules) -> List[str]:
    """把三层/两层/扁平责任模块归一为扁平功能名列表（与 AI EngineerProfile 口径一致）。"""
    import json as _json
    if isinstance(modules, str):
        try:
            modules = _json.loads(modules)
        except Exception:
            modules = {}
    if isinstance(modules, list):  # 旧扁平列表
        return [str(m) for m in modules if m]
    if isinstance(modules, dict):
        flat = []
        seen = set()
        for product, v in modules.items():
            if isinstance(v, list):
                items = [str(m) for m in v if m]
            elif isinstance(v, dict):
                items = [str(f) for fns in v.values() for f in (fns if isinstance(fns, list) else [fns]) if f]
            else:
                continue
            for it in items:
                if it not in seen:
                    seen.add(it)
                    flat.append(it)
        return flat
    return []


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
        # 解析部门名（department_id → 名称）
        from app.models.organization import Department
        dept_map = {d.id: d.name for d in db.query(Department).all()}
        out = []
        for r in records:
            dept_id = getattr(r, "department_id", None)
            dept_name = dept_map.get(dept_id) if dept_id else None
            if not dept_name:
                dept_name = getattr(r, "department", None)  # 旧字符串列兜底
            modules = _modules_to_flat(getattr(r, "responsibility_modules", None))
            duty = (getattr(r, "duty_text", None) or "").strip() or None
            job_level = getattr(r, "job_level", None) or None
            has_profile = bool(dept_name or modules or duty)
            out.append(AssignableUser(
                id=r.id,
                username=r.username,
                name=getattr(r, "name", None),
                status=getattr(r, "status", "inactive"),
                department=dept_name,
                job_level=job_level,
                modules=modules or None,
                duty=duty,
                has_profile=has_profile,
            ))
        return out
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取可指派人员列表失败: {str(e)}",
        )
    finally:
        db.close()
