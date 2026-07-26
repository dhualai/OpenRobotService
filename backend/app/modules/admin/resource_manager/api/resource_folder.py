from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Any
from app.core.database import get_async_db as get_db
from app.modules.admin.resource_manager.schemas.resource_folder import ResourceFolderCreate, ResourceFolderUpdate, ResourceFolderResponse, Child
from app.modules.admin.resource_manager.services.resource_folder_service import ResourceFolderService

router = APIRouter(prefix="/resource-folders", tags=["resource_folders"])


@router.get("/", response_model=List[ResourceFolderResponse])
async def get_all_folders(db: AsyncSession = Depends(get_db)):
    return await ResourceFolderService.get_all_folders(db)


@router.get("/root", response_model=List[ResourceFolderResponse])
async def get_root_folders(db: AsyncSession = Depends(get_db)):
    return await ResourceFolderService.get_root_folders(db)


@router.get("/root/children", response_model=List[Child])
async def get_root_children(db: AsyncSession = Depends(get_db)):
    return await ResourceFolderService.get_root_children(db)


@router.get("/{folder_id}", response_model=ResourceFolderResponse)
async def get_folder(folder_id: int, db: AsyncSession = Depends(get_db)):
    folder = await ResourceFolderService.get_folder_by_id(db, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹未找到")
    return folder


@router.get("/{folder_id}/children", response_model=List[Child])
async def get_folder_children(folder_id: int, db: AsyncSession = Depends(get_db)):
    if not await ResourceFolderService.get_folder_by_id(db, folder_id):
        raise HTTPException(status_code=404, detail="父文件夹未找到")
    return await ResourceFolderService.get_folder_children(db, folder_id)


@router.post("/", response_model=ResourceFolderResponse, status_code=201)
async def create_folder(folder_data: ResourceFolderCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await ResourceFolderService.create_folder(db, folder_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{folder_id}", response_model=ResourceFolderResponse)
async def update_folder(folder_id: int, folder_data: ResourceFolderUpdate, db: AsyncSession = Depends(get_db)):
    folder = await ResourceFolderService.update_folder(db, folder_id, folder_data)
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹未找到")
    return folder


@router.delete("/{folder_id}")
async def delete_folder(folder_id: int, db: AsyncSession = Depends(get_db)):
    try:
        success = await ResourceFolderService.delete_folder(db, folder_id)
        if not success:
            raise HTTPException(status_code=404, detail="文件夹未找到")
        return {"message": "文件夹已成功删除"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))