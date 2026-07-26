from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Any
from app.core.database import get_async_db as get_db
from app.modules.admin.resource_manager.schemas.resource import SyncBuildDeployRequest, ResourceResponse, ResourceUpdate, ResourceStats
from app.modules.admin.resource_manager.services.resource_service import ResourceService
from app.modules.admin.resource_manager.models.resource import ResourceType, StorageType
from app.utils.minio_client import minio_client

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("/", response_model=List[ResourceResponse])
async def get_all_resources(
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的最大记录数"),
    db: AsyncSession = Depends(get_db)
):
    return await ResourceService.get_all_resources(db, skip=skip, limit=limit)


@router.get("/recent", response_model=List[ResourceResponse])
async def get_recent_resources(
    limit: int = Query(10, ge=1, le=100, description="返回的最大记录数"),
    db: AsyncSession = Depends(get_db)
):
    return await ResourceService.get_recent_resources(db, limit=limit)


@router.get("/stats/summary", response_model=ResourceStats)
async def get_resource_stats(db: AsyncSession = Depends(get_db)):
    return await ResourceService.get_resource_stats(db)


@router.get("/stats/daily", response_model=Dict[str, Any])
async def get_resource_stats_by_date(
    days: int = Query(14, ge=1, le=90, description="统计天数，默认14天，最多90天"),
    db: AsyncSession = Depends(get_db)
):
    return await ResourceService.get_resource_stats_by_date(db, days=days)


@router.get("/{resource_id}", response_model=ResourceResponse)
async def get_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
    resource = await ResourceService.get_resource_by_id(db, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")
    return resource


@router.get("/hash/{hash_code}", response_model=ResourceResponse)
async def get_resource_by_hash(hash_code: str, db: AsyncSession = Depends(get_db)):
    resource = await ResourceService.get_resource_by_hash_code(db, hash_code)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")
    return resource


@router.get("/owner/{owner_id}", response_model=List[ResourceResponse])
async def get_resources_by_owner(
    owner_id: str,
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的最大记录数"),
    db: AsyncSession = Depends(get_db)
):
    return await ResourceService.get_resources_by_owner(db, owner_id, skip=skip, limit=limit)


@router.get("/type/{resource_type}", response_model=List[ResourceResponse])
async def get_resources_by_type(
    resource_type: ResourceType,
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的最大记录数"),
    db: AsyncSession = Depends(get_db)
):
    return await ResourceService.get_resources_by_type(db, resource_type, skip=skip, limit=limit)


@router.get("/category/{category}", response_model=List[ResourceResponse])
async def get_resources_by_category(
    category: str,
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的最大记录数"),
    db: AsyncSession = Depends(get_db)
):
    return await ResourceService.get_resources_by_category(db, category, skip=skip, limit=limit)


@router.get("/search/query", response_model=List[ResourceResponse])
async def search_resources(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的最大记录数"),
    db: AsyncSession = Depends(get_db)
):
    return await ResourceService.search_resources(db, q, skip=skip, limit=limit)


@router.post("/", response_model=ResourceResponse, status_code=201)
async def create_resource(
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    resource_labels: Optional[str] = Form(None),
    owner_id: str = Form(...),
    resource_type: ResourceType = Form(...),
    db: AsyncSession = Depends(get_db)
):
    try:
        import json
        labels = json.loads(resource_labels) if resource_labels else []
        return await ResourceService.create_resource(
            db,
            file=file,
            folder_id=folder_id,
            category=category,
            description=description,
            resource_labels=labels,
            owner_id=owner_id,
            resource_type=resource_type
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{resource_id}", response_model=ResourceResponse)
async def update_resource(
    resource_id: int,
    resource_data: ResourceUpdate,
    db: AsyncSession = Depends(get_db)
):
    resource = await ResourceService.update_resource(db, resource_id, resource_data)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")
    return resource


@router.delete("/{resource_id}")
async def delete_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
    success = await ResourceService.delete_resource(db, resource_id)
    if not success:
        raise HTTPException(status_code=404, detail="资源未找到")
    return {"message": "资源已成功删除"}


@router.post("/{resource_id}/download-count")
async def download_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
    resource = await ResourceService.increment_download_count(db, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")
    return {"message": "下载成功", "download_count": resource.download_count}


@router.get("/{resource_id}/download")
async def proxy_download_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db)
):
    resource = await ResourceService.get_resource_by_id(db, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")

    if not resource.is_available:
        raise HTTPException(status_code=403, detail="资源不可用")

    try:
            from minio.error import S3Error
            from urllib.parse import quote
            import mimetypes
            import asyncio

            if resource.storage_type == StorageType.OSS:
                from app.utils.oss_client import oss_client
                import alibabacloud_oss_v2 as oss

                bucket_name = resource.resource_url.split('/')[0]
                object_name = '/'.join(resource.resource_url.split('/')[1:])

                object_info = await asyncio.to_thread(
                    oss_client.get_file_info, resource.resource_url
                )
                if not object_info:
                    raise HTTPException(status_code=404, detail="文件不存在")

                content_type = getattr(object_info, 'content_type', None)
                if not content_type:
                    content_type, _ = mimetypes.guess_type(resource.resource_name)
                    if not content_type:
                        content_type = 'application/octet-stream'

                file_size = getattr(object_info, 'content_length', 0)

                result = await asyncio.to_thread(
                    oss_client.client.get_object,
                    oss.GetObjectRequest(bucket=bucket_name, key=object_name)
                )

                filename = quote(resource.resource_name, safe='')
                content_disposition = f'inline; filename="{filename}"'

                return StreamingResponse(
                    content=result.body.iter_chunks(),
                    media_type=content_type,
                    headers={
                        "Content-Disposition": content_disposition,
                        "Content-Length": str(file_size),
                        "Content-Type": content_type,
                        "Cache-Control": "no-cache",
                        "Accept-Ranges": "bytes"
                    }
                )
            else:
                bucket_name = resource.resource_url.split('/')[0]
                object_name = '/'.join(resource.resource_url.split('/')[1:])

                object_info = await asyncio.to_thread(
                    minio_client.client.stat_object, bucket_name, object_name
                )

                content_type = object_info.content_type
                if not content_type:
                    content_type, _ = mimetypes.guess_type(resource.resource_name)
                    if not content_type:
                        content_type = 'application/octet-stream'

                response = await asyncio.to_thread(
                    minio_client.client.get_object, bucket_name, object_name
                )

                filename = quote(resource.resource_name, safe='')
                content_disposition = f'inline; filename="{filename}"'

                return StreamingResponse(
                    content=response.stream(64 * 1024),
                    media_type=content_type,
                    headers={
                    "Content-Disposition": content_disposition,
                    "Content-Length": str(object_info.size),
                    "Content-Type": content_type,
                    "Cache-Control": "no-cache",
                    "Accept-Ranges": "bytes"
                }
            )
    except S3Error as e:
        if e.code == 'NoSuchKey':
            raise HTTPException(status_code=404, detail="文件不存在")
        else:
            raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@router.get("/{resource_id}/download-url")
async def get_resource_download_url(
    resource_id: int,
    expires_minutes: int = Query(5, ge=1, le=10080, description="URL有效期（分钟），默认5分钟，最大10080分钟（7天）"),
    db: AsyncSession = Depends(get_db)
):
    resource = await ResourceService.get_resource_by_id(db, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")

    download_url = ResourceService.get_download_url(resource, expires_minutes)
    return {
        "resource_id": resource.id,
        "resource_name": resource.resource_name,
        "download_url": download_url,
        "expires_in_minutes": expires_minutes
    }


@router.get("/{resource_id}/thumbnail-url")
async def get_resource_thumbnail_url(
    resource_id: int,
    expires_minutes: int = Query(5, ge=1, le=10080, description="URL有效期（分钟），默认5分钟，最大10080分钟（7天）"),
    db: AsyncSession = Depends(get_db)
):
    resource = await ResourceService.get_resource_by_id(db, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")

    if not resource.thumbnail_url:
        raise HTTPException(status_code=404, detail="该资源没有缩略图")

    thumbnail_url = ResourceService.get_thumbnail_url(resource, expires_minutes)
    return {
        "resource_id": resource.id,
        "resource_name": resource.resource_name,
        "thumbnail_url": thumbnail_url,
        "expires_in_minutes": expires_minutes
    }


@router.get("/{resource_id}/preview-url")
async def get_resource_preview_url(
    resource_id: int,
    expires_minutes: int = Query(5, ge=1, le=10080, description="URL有效期（分钟），默认5分钟，最大10080分钟（7天）"),
    db: AsyncSession = Depends(get_db)
):
    resource = await ResourceService.get_resource_by_id(db, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="资源未找到")

    if not resource.preview_url:
        raise HTTPException(status_code=404, detail="该资源没有预览")

    preview_url = ResourceService.get_preview_url(resource, expires_minutes)
    return {
        "resource_id": resource.id,
        "resource_name": resource.resource_name,
        "preview_url": preview_url,
        "expires_in_minutes": expires_minutes
    }


@router.post("/{resource_id}/like", response_model=Dict[str, Any])
async def like_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
    result = await ResourceService.toggle_like(db, resource_id)
    if not result:
        raise HTTPException(status_code=404, detail="资源未找到")
    return result


@router.post("/sync-build-deploy")
async def sync_build_deploy(
    request: SyncBuildDeployRequest
):
    try:
        return await ResourceService.sync_md_files_and_build(
            execute_nginx_reload=request.execute_nginx_reload
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-oss")
async def sync_oss_resources(
    folder_id: Optional[int] = Query(None, description="文件夹ID，如果为None则同步所有OSS文件"),
    owner_id: str = Query("system", description="资源所有者ID，默认为system"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await ResourceService.sync_oss_resources(db, folder_id=folder_id, owner_id=owner_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))