from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import re
from app.modules.admin.resource_manager.models.resource import Resource, ResourceStatus, ResourceType, StorageType
from app.modules.admin.resource_manager.models.resource_folder import ResourceFolder
from app.modules.admin.resource_manager.schemas.resource import ResourceCreate, ResourceUpdate, ResourceStats
from app.modules.admin.resource_manager.schemas.resource_folder import ResourceFolderCreate
from app.modules.admin.resource_manager.services.resource_folder_service import ResourceFolderService
from app.utils.database_utils import DatabaseUtils
from app.utils.data_utils import sanitize_input
from app.utils.minio_client import minio_client


def is_valid_id(id_value):
    return isinstance(id_value, int) and id_value > 0


class ResourceService:

    @staticmethod
    async def sync_folder_structure(db: AsyncSession, oss_key: str) -> Optional[int]:
        parts = oss_key.split('/')
        if len(parts) <= 1:
            return None

        folder_parts = parts[:-1]
        if not folder_parts:
            return None

        current_parent_id = None

        for folder_name in folder_parts:
            result = await db.execute(
                select(ResourceFolder)
                .where(and_(
                    ResourceFolder.folder_name == folder_name,
                    ResourceFolder.parent_id == current_parent_id,
                    ResourceFolder.deleted_at == None
                ))
            )
            folder = result.scalars().first()
            
            if folder:
                current_parent_id = folder.id
            else:
                folder_data = ResourceFolderCreate(
                    folder_name=folder_name,
                    parent_id=current_parent_id,
                    description=None,
                    tags=None
                )
                created_folder = await ResourceFolderService.create_folder(db, folder_data)
                current_parent_id = created_folder["id"]

        return current_parent_id

    @staticmethod
    async def get_all_resources(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Resource]:
        result = await db.execute(
            select(Resource)
            .where(Resource.deleted_at == None)
            .order_by(Resource.created_at.desc())
            .offset(skip).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_resource_by_id(db: AsyncSession, resource_id: int, update_stats: bool = False) -> Optional[Resource]:
        if not is_valid_id(resource_id):
            return None

        result = await db.execute(
            select(Resource)
            .where(and_(Resource.id == resource_id, Resource.deleted_at == None))
        )
        resource = result.scalars().first()

        if resource and update_stats:
            resource.accessed_at = datetime.now()
            resource.view_count += 1
            await db.commit()

        return resource

    @staticmethod
    async def get_resource_by_hash_code(db: AsyncSession, hash_code: str) -> Optional[Resource]:
        if not hash_code:
            return None

        result = await db.execute(
            select(Resource)
            .where(and_(Resource.resource_hash_code == hash_code, Resource.deleted_at == None))
        )
        return result.scalars().first()

    @staticmethod
    async def get_resources_by_owner(db: AsyncSession, owner_id: str, skip: int = 0, limit: int = 100) -> List[Resource]:
        if not owner_id:
            return []

        result = await db.execute(
            select(Resource)
            .where(and_(Resource.owner_id == owner_id, Resource.deleted_at == None))
            .order_by(Resource.created_at.desc())
            .offset(skip).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_resources_by_type(db: AsyncSession, resource_type: ResourceType, skip: int = 0, limit: int = 100) -> List[Resource]:
        result = await db.execute(
            select(Resource)
            .where(and_(Resource.resource_type == resource_type, Resource.deleted_at == None))
            .order_by(Resource.created_at.desc())
            .offset(skip).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_resources_by_category(db: AsyncSession, category: str, skip: int = 0, limit: int = 100) -> List[Resource]:
        if not category:
            return []

        result = await db.execute(
            select(Resource)
            .where(and_(Resource.category == category, Resource.deleted_at == None))
            .order_by(Resource.created_at.desc())
            .offset(skip).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def search_resources(db: AsyncSession, query: str, skip: int = 0, limit: int = 100) -> List[Resource]:
        if not query:
            return []

        search_term = f"%{sanitize_input(query)}%"
        result = await db.execute(
            select(Resource)
            .where(
                and_(
                    Resource.deleted_at == None,
                    or_(
                        Resource.resource_name.ilike(search_term),
                        Resource.description.ilike(search_term),
                        Resource.category.ilike(search_term)
                    )
                )
            )
            .order_by(Resource.created_at.desc())
            .offset(skip).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def create_resource(
        db: AsyncSession,
        file,
        folder_id: Optional[str] = None,
        category: Optional[str] = None,
        description: Optional[str] = None,
        resource_labels: Optional[List[str]] = None,
        owner_id: str = None,
        resource_type: ResourceType = None
    ) -> Resource:
        import os
        import mimetypes
        from app.core.config import settings

        resource_hash_code = str(uuid.uuid4())[:8]
        resource_name = sanitize_input(file.filename)
        file_extension = os.path.splitext(resource_name)[1].lower().lstrip('.')
        content_type = file.content_type or mimetypes.guess_type(resource_name)[0] or 'application/octet-stream'

        folder = None
        folder_path = ""
        folder_id_int = None
        if folder_id:
            try:
                folder_id_int = int(folder_id)
                folder_result = await db.execute(
                    select(ResourceFolder).where(ResourceFolder.id == folder_id_int)
                )
                folder = folder_result.scalars().first()
                if folder:
                    folder_path = folder.full_path.lstrip('/').rstrip('/')
            except (ValueError, Exception) as e:
                print(f"获取文件夹信息失败: {e}")

        if not folder_path or not folder:
            folder_path = "/"

        file_content = await file.read()
        file_size = len(file_content)

        OSS_THRESHOLD = 1 * 1024 * 1024 * 1024
        use_oss = file_size > OSS_THRESHOLD

        if use_oss:
            from app.utils.oss_client import oss_client
            oss_config = oss_client.config
            oss_bucket = oss_config['bucket_name']
            oss_upload_dir = oss_config.get('upload_dir', '').rstrip('/')

            oss_object_name = f"{oss_upload_dir}/{folder_path.lstrip('/')}/{resource_name}"
            oss_object_name = re.sub(r'/+', '/', oss_object_name)
            resource_url = f"{oss_bucket}/{oss_object_name}"

            import asyncio
            upload_success = await asyncio.to_thread(
                oss_client.upload_bytes, file_content, resource_url, content_type
            )

            if upload_success:
                file_info = await asyncio.to_thread(
                    oss_client.get_file_info, resource_url
                )
                if not file_info:
                    print(f"警告：无法获取OSS文件信息，使用本地文件信息")
                    file_info = type('obj', (object,), {
                        'size': file_size,
                        'content_type': content_type
                    })
            else:
                raise Exception("上传到OSS失败，请检查磁盘空间或OSS配置")

            storage_type = StorageType.OSS
        else:
            object_name = f"{folder_path}/{resource_name}"
            resource_url = f"{settings.MINIO_BUCKET}/{object_name}"
            resource_url = re.sub(r'/+', '/', resource_url)

            import asyncio
            upload_success = await asyncio.to_thread(
                minio_client.upload_bytes, file_content, resource_url, content_type
            )

            if upload_success:
                file_info = await asyncio.to_thread(
                    minio_client.get_file_info, resource_url
                )
                if not file_info:
                    print(f"警告：无法获取MinIO文件信息，使用本地文件信息")
                    file_info = type('obj', (object,), {
                        'size': file_size,
                        'content_type': content_type
                    })
            else:
                raise Exception("上传到MinIO失败，请检查磁盘空间或MinIO配置")

            storage_type = StorageType.MINIO

        try:
            if storage_type == StorageType.OSS:
                file_size_value = getattr(file_info, 'content_length', file_size)
            else:
                file_size_value = getattr(file_info, 'size', file_size)

            validated_data = {
                "resource_name": resource_name,
                "resource_hash_code": resource_hash_code,
                "owner_id": sanitize_input(owner_id),
                "resource_type": resource_type,
                "resource_status": ResourceStatus.PROCESSING,
                "resource_url": resource_url,
                "resource_format": file_extension,
                "resource_size": file_size_value,
                "storage_type": storage_type,
                "category": sanitize_input(category) if category else None,
                "resource_labels": resource_labels or [],
                "description": sanitize_input(description) if description else None,
                "thumbnail_url": None,
                "preview_url": None,
                "folder_id": folder_id_int if folder_id else None,
                "extra_metadata": {
                    "content_type": content_type,
                    "original_filename": resource_name
                },
                "view_count": 0,
                "download_count": 0,
                "like_count": 0,
                "favorite_count": 0
            }

            resource = await DatabaseUtils.create_and_commit(db, Resource, **validated_data)

            if folder:
                try:
                    if folder.child_resource_ids is None:
                        folder.child_resource_ids = []

                    child_resource_ids = list(folder.child_resource_ids)

                    if resource.id not in child_resource_ids:
                        child_resource_ids.append(resource.id)
                        folder.child_resource_ids = child_resource_ids

                        folder.direct_resource_count += 1
                        folder.resource_count += 1
                        folder.total_size += resource.resource_size

                        await db.commit()
                except Exception as e:
                    print(f"更新文件夹资源列表失败: {e}")
                    await db.rollback()

            return resource

        finally:
            pass

    @staticmethod
    async def update_resource(db: AsyncSession, resource_id: int, resource_data: ResourceUpdate) -> Optional[Resource]:
        resource = await ResourceService.get_resource_by_id(db, resource_id)
        if not resource:
            return None

        update_data = resource_data.model_dump(exclude_unset=True)

        if "resource_name" in update_data:
            update_data["resource_name"] = sanitize_input(update_data["resource_name"])

        if "category" in update_data:
            update_data["category"] = sanitize_input(update_data["category"])

        if "description" in update_data:
            update_data["description"] = sanitize_input(update_data["description"])

        if "thumbnail_url" in update_data:
            update_data["thumbnail_url"] = sanitize_input(update_data["thumbnail_url"])

        if "preview_url" in update_data:
            update_data["preview_url"] = sanitize_input(update_data["preview_url"])

        for field, value in update_data.items():
            setattr(resource, field, value)

        return await DatabaseUtils.commit_and_refresh(db, resource)

    @staticmethod
    async def delete_resource(db: AsyncSession, resource_id: int) -> bool:
        resource = await ResourceService.get_resource_by_id(db, resource_id)
        if not resource:
            return False

        try:
            if resource.storage_type == StorageType.OSS:
                from app.utils.oss_client import oss_client
                oss_client.delete_file(resource.resource_url)
            else:
                minio_client.delete_file(resource.resource_url)
        except Exception as e:
            print(f"删除存储文件失败: {e}")

        resource.deleted_at = datetime.now()
        await db.commit()
        return True

    @staticmethod
    async def increment_download_count(db: AsyncSession, resource_id: int) -> Optional[Resource]:
        resource = await ResourceService.get_resource_by_id(db, resource_id)
        if not resource:
            return None

        resource.download_count += 1
        resource.accessed_at = datetime.now()
        return await DatabaseUtils.commit_and_refresh(db, resource)

    @staticmethod
    async def toggle_like(db: AsyncSession, resource_id: int) -> Optional[Dict[str, Any]]:
        resource = await ResourceService.get_resource_by_id(db, resource_id)
        if not resource:
            return None

        resource.like_count += 1
        await db.commit()
        await db.refresh(resource)

        return {"id": resource.id, "like_count": resource.like_count}

    @staticmethod
    async def get_resource_stats(db: AsyncSession) -> ResourceStats:
        total_result = await db.execute(
            select(func.count(Resource.id)).where(Resource.deleted_at == None)
        )
        total_resources = total_result.scalar() or 0

        available_result = await db.execute(
            select(func.count(Resource.id))
            .where(and_(Resource.deleted_at == None, Resource.resource_status == ResourceStatus.AVAILABLE))
        )
        available_resources = available_result.scalar() or 0

        size_result = await db.execute(
            select(func.sum(Resource.resource_size)).where(Resource.deleted_at == None)
        )
        total_size = size_result.scalar() or 0

        type_result = await db.execute(
            select(Resource.resource_type, func.count(Resource.id))
            .where(Resource.deleted_at == None)
            .group_by(Resource.resource_type)
        )
        type_distribution = {item[0]: item[1] for item in type_result.all()}

        for resource_type in ResourceType:
            if resource_type not in type_distribution:
                type_distribution[resource_type] = 0

        return ResourceStats(
            total_resources=total_resources,
            available_resources=available_resources,
            total_size=total_size,
            type_distribution=type_distribution
        )

    @staticmethod
    async def get_recent_resources(db: AsyncSession, limit: int = 10) -> List[Resource]:
        result = await db.execute(
            select(Resource)
            .where(Resource.deleted_at == None)
            .order_by(Resource.updated_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_resource_stats_by_date(db: AsyncSession, days: int = 14) -> Dict[str, Any]:
        from datetime import datetime, timedelta

        now = datetime.now()
        start_date = now - timedelta(days=days)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        daily_stats = []
        for i in range(days):
            date = start_date + timedelta(days=i)
            daily_stats.append({
                'date': date.strftime('%Y-%m-%d'),
                'created_count': 0,
                'updated_count': 0,
                'created_resources': [],
                'updated_resources': []
            })

        created_result = await db.execute(
            select(Resource)
            .where(
                and_(
                    Resource.deleted_at == None,
                    Resource.created_at >= start_date,
                    Resource.created_at < start_date + timedelta(days=days)
                )
            )
            .order_by(Resource.created_at.desc())
        )
        created_resources = created_result.scalars().all()

        created_by_date = {}
        for resource in created_resources:
            date_str = resource.created_at.strftime('%Y-%m-%d')
            if date_str not in created_by_date:
                created_by_date[date_str] = []
            if len(created_by_date[date_str]) < 10:
                created_by_date[date_str].append({
                    'id': resource.id,
                    'name': resource.resource_name,
                    'type': resource.resource_type,
                    'size': resource.resource_size
                })

        updated_result = await db.execute(
            select(Resource)
            .where(
                and_(
                    Resource.deleted_at == None,
                    Resource.updated_at >= start_date,
                    Resource.updated_at < start_date + timedelta(days=days)
                )
            )
            .order_by(Resource.updated_at.desc())
        )
        updated_resources = updated_result.scalars().all()

        updated_by_date = {}
        for resource in updated_resources:
            date_str = resource.updated_at.strftime('%Y-%m-%d')
            if date_str not in updated_by_date:
                updated_by_date[date_str] = []
            if len(updated_by_date[date_str]) < 10:
                updated_by_date[date_str].append({
                    'id': resource.id,
                    'name': resource.resource_name,
                    'type': resource.resource_type,
                    'size': resource.resource_size
                })

        for item in daily_stats:
            date_str = item['date']
            item['created_count'] = len(created_by_date.get(date_str, []))
            item['updated_count'] = len(updated_by_date.get(date_str, []))
            item['created_resources'] = created_by_date.get(date_str, [])
            item['updated_resources'] = updated_by_date.get(date_str, [])

        return {
            'daily_stats': daily_stats,
            'days': days
        }

    @staticmethod
    def get_download_url(resource: Resource, expires_minutes: int = 5) -> str:
        if not resource.resource_url:
            return ""

        if resource.storage_type == StorageType.OSS:
            from app.utils.oss_client import oss_client
            return oss_client.get_presigned_url(resource.resource_url, expires_minutes)
        else:
            return minio_client.get_presigned_url(resource.resource_url, expires_minutes)

    @staticmethod
    def get_thumbnail_url(resource: Resource, expires_minutes: int = 5) -> str:
        if not resource.thumbnail_url:
            return ""

        if resource.storage_type == StorageType.OSS:
            from app.utils.oss_client import oss_client
            return oss_client.get_presigned_url(resource.thumbnail_url, expires_minutes)
        else:
            return minio_client.get_presigned_url(resource.thumbnail_url, expires_minutes)

    @staticmethod
    def get_preview_url(resource: Resource, expires_minutes: int = 5) -> str:
        if not resource.preview_url:
            return ""

        if resource.storage_type == StorageType.OSS:
            from app.utils.oss_client import oss_client
            return oss_client.get_presigned_url(resource.preview_url, expires_minutes)
        else:
            return minio_client.get_presigned_url(resource.preview_url, expires_minutes)

    @staticmethod
    def get_upload_url(object_path: str, expires_minutes: int = 60) -> str:
        return minio_client.get_presigned_put_url(object_path, expires_minutes)

    @staticmethod
    def upload_file(file_path: str, object_path: str, content_type: Optional[str] = None) -> bool:
        return minio_client.upload_file(file_path, object_path, content_type)

    @staticmethod
    def delete_file(object_path: str) -> bool:
        return minio_client.delete_file(object_path)

    @staticmethod
    async def sync_oss_resources(
        db: AsyncSession,
        folder_id: Optional[int] = None,
        owner_id: str = "system"
    ) -> Dict[str, Any]:
        import os
        import asyncio

        try:
            from app.utils.oss_client import oss_client
            oss_files = await asyncio.to_thread(oss_client.list_files)
            
            if not oss_files:
                return {
                    "status": "success",
                    "message": "OSS中没有文件",
                    "oss_files_count": 0,
                    "added": 0,
                    "deleted": 0,
                    "updated": 0,
                    "added_files": [],
                    "deleted_files": []
                }

            if folder_id:
                result = await db.execute(
                    select(Resource)
                    .where(and_(Resource.folder_id == folder_id, Resource.deleted_at == None))
                )
                db_resources = result.scalars().all()
            else:
                result = await db.execute(
                    select(Resource)
                    .where(and_(Resource.storage_type == StorageType.OSS, Resource.deleted_at == None))
                )
                db_resources = result.scalars().all()

            db_resource_map = {}
            bucket_name = oss_client.config['bucket_name']
            for resource in db_resources:
                if resource.resource_url:
                    if resource.resource_url.startswith(f"{bucket_name}/"):
                        object_name = resource.resource_url[len(f"{bucket_name}/"):]
                        db_resource_map[object_name] = resource

            added_files = []
            deleted_files = []
            updated_files = []

            for oss_file in oss_files:
                oss_key = oss_file['key']
                if oss_key not in db_resource_map:
                    resource_name = os.path.basename(oss_key)
                    file_extension = os.path.splitext(resource_name)[1].lower().lstrip('.')
                    
                    resource_type = ResourceService._get_resource_type_by_extension(file_extension)
                    
                    resource_url = f"{bucket_name}/{oss_key}"
                    
                    resource_hash_code = str(uuid.uuid4())[:8]
                    
                    file_info = await asyncio.to_thread(
                        oss_client.get_file_info, resource_url
                    )
                    
                    file_size = oss_file['size']
                    content_type = oss_file.get('content_type') or getattr(file_info, 'content_type', None)
                    
                    effective_folder_id = folder_id
                    if effective_folder_id is None:
                        effective_folder_id = await ResourceService.sync_folder_structure(db, oss_key)
                    
                    validated_data = {
                        "resource_name": resource_name,
                        "resource_hash_code": resource_hash_code,
                        "owner_id": owner_id,
                        "resource_type": resource_type,
                        "resource_status": ResourceStatus.AVAILABLE,
                        "resource_url": resource_url,
                        "resource_format": file_extension,
                        "resource_size": file_size,
                        "storage_type": StorageType.OSS,
                        "category": None,
                        "resource_labels": [],
                        "description": None,
                        "thumbnail_url": None,
                        "preview_url": None,
                        "folder_id": effective_folder_id,
                        "extra_metadata": {
                            "content_type": content_type,
                            "original_filename": resource_name
                        },
                        "view_count": 0,
                        "download_count": 0,
                        "like_count": 0,
                        "favorite_count": 0
                    }
                    
                    resource = await DatabaseUtils.create_and_commit(db, Resource, **validated_data)
                    added_files.append({
                        "id": resource.id,
                        "name": resource.resource_name,
                        "url": resource.resource_url
                    })

                    if effective_folder_id:
                        folder = await DatabaseUtils.get_by_id(db, ResourceFolder, effective_folder_id)
                        if folder:
                            if folder.child_resource_ids is None:
                                folder.child_resource_ids = []

                            child_resource_ids = list(folder.child_resource_ids)

                            if resource.id not in child_resource_ids:
                                child_resource_ids.append(resource.id)
                                folder.child_resource_ids = child_resource_ids

                                folder.direct_resource_count += 1
                                folder.resource_count += 1
                                folder.total_size += resource.resource_size

                                await db.commit()

            for oss_key, resource in db_resource_map.items():
                found_in_oss = False
                for oss_file in oss_files:
                    if oss_file['key'] == oss_key:
                        found_in_oss = True
                        break
                
                if not found_in_oss:
                    resource.deleted_at = datetime.now()
                    deleted_files.append({
                        "id": resource.id,
                        "name": resource.resource_name,
                        "url": resource.resource_url
                    })

                    if resource.folder_id:
                        folder = await DatabaseUtils.get_by_id(db, ResourceFolder, resource.folder_id)
                        if folder and folder.child_resource_ids:
                            child_resource_ids = list(folder.child_resource_ids)

                            if resource.id in child_resource_ids:
                                child_resource_ids.remove(resource.id)
                                folder.child_resource_ids = child_resource_ids

                                folder.direct_resource_count -= 1
                                folder.resource_count -= 1
                                folder.total_size -= resource.resource_size

            if deleted_files:
                await db.commit()

            return {
                "status": "success",
                "message": "同步完成",
                "folder_id": folder_id,
                "oss_files_count": len(oss_files),
                "db_resources_count": len(db_resources),
                "added": len(added_files),
                "deleted": len(deleted_files),
                "updated": len(updated_files),
                "added_files": added_files,
                "deleted_files": deleted_files
            }

        except Exception as e:
            await db.rollback()
            return {
                "status": "error",
                "message": f"同步失败: {str(e)}",
                "oss_files_count": 0,
                "added": 0,
                "deleted": 0,
                "updated": 0,
                "added_files": [],
                "deleted_files": []
            }

    @staticmethod
    def _get_resource_type_by_extension(extension: str) -> ResourceType:
        extension = extension.lower()
        
        image_extensions = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'}
        video_extensions = {'mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv'}
        audio_extensions = {'mp3', 'wav', 'ogg', 'flac', 'aac'}
        document_extensions = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt'}
        archive_extensions = {'zip', 'rar', '7z', 'tar', 'gz'}
        
        if extension in image_extensions:
            return ResourceType.IMAGE
        elif extension in video_extensions:
            return ResourceType.VIDEO
        elif extension in audio_extensions:
            return ResourceType.AUDIO
        elif extension in document_extensions:
            return ResourceType.DOCUMENT
        elif extension in archive_extensions:
            return ResourceType.ARCHIVE
        else:
            return ResourceType.FILE

    @staticmethod
    async def sync_md_files_and_build(
        execute_nginx_reload: bool = False
    ) -> Dict[str, Any]:
        import os
        import shutil
        import subprocess
        from app.core.config import settings
        
        try:
            md_files = []
            
            try:
                objects = minio_client.client.list_objects(
                    settings.MINIO_BUCKET,
                    recursive=True
                )
                
                for obj in objects:
                    if obj.object_name.endswith('.md'):
                        md_files.append(obj.object_name)
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"获取MinIO文件列表失败: {str(e)}"
                }
            
            if not md_files:
                return {
                    "status": "success",
                    "message": "MinIO中没有MD文件"
                }
            
            frontend_md_dir = os.path.join(settings.FRONTEND_DIR, 'docs')
            os.makedirs(frontend_md_dir, exist_ok=True)
            
            for md_file in md_files:
                try:
                    response = minio_client.client.get_object(settings.MINIO_BUCKET, md_file)
                    content = response.read().decode('utf-8')
                    
                    local_path = os.path.join(frontend_md_dir, os.path.basename(md_file))
                    with open(local_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                except Exception as e:
                    print(f"下载MD文件失败 {md_file}: {e}")
            
            build_result = subprocess.run(
                ['npm', 'run', 'build'],
                cwd=settings.FRONTEND_DIR,
                capture_output=True,
                text=True
            )
            
            if build_result.returncode != 0:
                return {
                    "status": "error",
                    "message": f"构建失败: {build_result.stderr}"
                }
            
            dist_dir = os.path.join(settings.FRONTEND_DIR, 'dist')
            nginx_dir = settings.NGINX_DIR
            
            if os.path.exists(nginx_dir):
                shutil.rmtree(nginx_dir)
            shutil.copytree(dist_dir, nginx_dir)
            
            if execute_nginx_reload:
                subprocess.run(['nginx', '-s', 'reload'], capture_output=True)
            
            return {
                "status": "success",
                "message": "同步、构建和部署完成",
                "md_files_count": len(md_files),
                "build_output": build_result.stdout
            }
        
        except Exception as e:
            return {
                "status": "error",
                "message": f"同步构建部署失败: {str(e)}"
            }