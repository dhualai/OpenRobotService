"""外部任务源账号映射 CRUD（INTEGRATION_DESIGN.md §4.3）。

挂 /api/admin/task-user-mappings，跨源通用。供后台维护「外部账号 → 本平台 user_id」。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.task import TaskUserMapping

router = APIRouter(prefix="/task-user-mappings", tags=["integrations"])


class MappingCreate(BaseModel):
    source: str
    external_account: str
    external_realname: Optional[str] = None
    local_user_id: str


class MappingUpdate(BaseModel):
    external_realname: Optional[str] = None
    local_user_id: Optional[str] = None


class MappingResponse(BaseModel):
    id: int
    source: str
    external_account: str
    external_realname: Optional[str]
    local_user_id: str

    class Config:
        from_attributes = True


@router.get("", response_model=List[MappingResponse])
async def list_mappings(
    source: Optional[str] = Query(None, description="按任务源过滤"),
    db: AsyncSession = Depends(get_async_db),
    _=Depends(get_current_active_user_from_token),
):
    stmt = select(TaskUserMapping).order_by(TaskUserMapping.source, TaskUserMapping.external_account)
    if source:
        stmt = stmt.where(TaskUserMapping.source == source)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=MappingResponse)
async def create_mapping(
    data: MappingCreate,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(get_current_active_user_from_token),
):
    mapping = TaskUserMapping(**data.dict())
    db.add(mapping)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="映射已存在（source + external_account 唯一）")
    await db.refresh(mapping)
    return mapping


@router.put("/{mapping_id}", response_model=MappingResponse)
async def update_mapping(
    mapping_id: int,
    data: MappingUpdate,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(get_current_active_user_from_token),
):
    mapping = (
        await db.execute(select(TaskUserMapping).where(TaskUserMapping.id == mapping_id))
    ).scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="映射不存在")
    for k, v in data.dict(exclude_unset=True).items():
        setattr(mapping, k, v)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.delete("/{mapping_id}")
async def delete_mapping(
    mapping_id: int,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(get_current_active_user_from_token),
):
    mapping = (
        await db.execute(select(TaskUserMapping).where(TaskUserMapping.id == mapping_id))
    ).scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="映射不存在")
    await db.delete(mapping)
    await db.commit()
    return {"message": "删除成功"}
