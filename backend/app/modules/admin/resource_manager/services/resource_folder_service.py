from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional, Dict, Any
from app.modules.admin.resource_manager.models.resource_folder import ResourceFolder
from app.modules.admin.resource_manager.models.resource import Resource
from app.modules.admin.resource_manager.schemas.resource_folder import ResourceFolderCreate, ResourceFolderUpdate
from app.utils.database_utils import DatabaseUtils
from sqlalchemy.orm.attributes import flag_modified
from app.utils.data_utils import sanitize_input
from app.utils.minio_client import minio_client
from app.core.config import settings


def is_valid_id(id_value):
    return isinstance(id_value, int) and id_value > 0


class ResourceFolderService:

    @staticmethod
    async def get_all_folders(db: AsyncSession) -> List[Dict[str, Any]]:
        result = await db.execute(select(ResourceFolder).order_by(ResourceFolder.path))
        folders = result.scalars().all()
        
        return [await ResourceFolderService._convert_folder_response(db, folder) for folder in folders]

    @staticmethod
    async def get_folder_by_id(db: AsyncSession, folder_id: int) -> Optional[Dict[str, Any]]:
        if not is_valid_id(folder_id):
            return None
        folder = await DatabaseUtils.get_by_id(db, ResourceFolder, folder_id)
        if folder:
            return await ResourceFolderService._convert_folder_response(db, folder)
        return None

    @staticmethod
    async def get_folder_children(db: AsyncSession, parent_id: int) -> List[Dict[str, Any]]:
        if not is_valid_id(parent_id):
            return []

        parent_folder = await DatabaseUtils.get_by_id(db, ResourceFolder, parent_id)
        if not parent_folder:
            return []

        children = []

        if parent_folder.child_folder_ids:
            result = await db.execute(
                select(ResourceFolder.id, ResourceFolder.folder_name, ResourceFolder.updated_at, ResourceFolder.deleted_at)
                .where(ResourceFolder.id.in_(parent_folder.child_folder_ids))
                .order_by(ResourceFolder.folder_name)
            )
            child_folders = result.all()
            for folder in child_folders:
                children.append({
                    "id": folder.id,
                    "name": folder.folder_name,
                    "child_type": "folder",
                    "updated_at": folder.updated_at,
                    "deleted_at": folder.deleted_at
                })

        if parent_folder.child_resource_ids:
            result = await db.execute(
                select(Resource.id, Resource.resource_name, Resource.updated_at, Resource.deleted_at, Resource.resource_size, Resource.storage_type, Resource.resource_status)
                .where(Resource.id.in_(parent_folder.child_resource_ids))
                .where(Resource.deleted_at == None)
                .order_by(Resource.resource_name)
            )
            child_resources = result.all()
            for resource in child_resources:
                children.append({
                    "id": resource.id,
                    "name": resource.resource_name,
                    "child_type": "resource",
                    "updated_at": resource.updated_at,
                    "deleted_at": resource.deleted_at,
                    "resource_size": resource.resource_size,
                    "storage_type": resource.storage_type,
                    "resource_status": resource.resource_status
                })

        return children


    @staticmethod
    async def get_root_folders(db: AsyncSession) -> List[Dict[str, Any]]:
        result = await db.execute(
            select(ResourceFolder)
            .where(ResourceFolder.parent_id == None)
            .order_by(ResourceFolder.folder_name)
        )
        folders = result.scalars().all()
        
        return [await ResourceFolderService._convert_folder_response(db, folder) for folder in folders]
    
    @staticmethod
    async def get_root_children(db: AsyncSession) -> List[Dict[str, Any]]:
        children = []
        
        result = await db.execute(
            select(ResourceFolder.id, ResourceFolder.folder_name, ResourceFolder.updated_at, ResourceFolder.deleted_at)
            .where(ResourceFolder.parent_id == None)
            .order_by(ResourceFolder.folder_name)
        )
        root_folders = result.all()
        
        for folder in root_folders:
            children.append({
                "id": folder.id,
                "name": folder.folder_name,
                "child_type": "folder",
                "updated_at": folder.updated_at,
                "deleted_at": folder.deleted_at
            })
        
        result = await db.execute(
            select(Resource.id, Resource.resource_name, Resource.updated_at, Resource.deleted_at, Resource.resource_size, Resource.storage_type, Resource.resource_status)
            .where(Resource.folder_id == None)
            .where(Resource.deleted_at == None)
            .order_by(Resource.resource_name)
        )
        root_resources = result.all()
        
        for resource in root_resources:
            children.append({
                "id": resource.id,
                "name": resource.resource_name,
                "child_type": "resource",
                "updated_at": resource.updated_at,
                "deleted_at": resource.deleted_at,
                "resource_size": resource.resource_size,
                "storage_type": resource.storage_type,
                "resource_status": resource.resource_status
            })
        
        return children
    
    @staticmethod
    async def _convert_folder_response(db: AsyncSession, folder: ResourceFolder) -> Dict[str, Any]:
        folder_dict = {
            "id": folder.id,
            "folder_name": folder.folder_name,
            "parent_id": folder.parent_id,
            "description": folder.description,
            "tags": folder.tags,
            "path": folder.path,
            "level": folder.level,
            "resource_count": folder.resource_count,
            "direct_resource_count": folder.direct_resource_count,
            "folder_count": folder.folder_count,
            "total_size": folder.total_size,
            "created_at": folder.created_at,
            "updated_at": folder.updated_at,
            "deleted_at": folder.deleted_at,
            "accessed_at": folder.accessed_at
        }
        
        children = []
        
        if folder.child_folder_ids:
            result = await db.execute(
                select(ResourceFolder).where(ResourceFolder.id.in_(folder.child_folder_ids))
            )
            child_folders_map = {f.id: f for f in result.scalars().all()}
            
            for child_id in folder.child_folder_ids:
                child_folder = child_folders_map.get(child_id)
                if child_folder:
                    children.append({
                        "id": child_folder.id,
                        "name": child_folder.folder_name,
                        "child_type": "folder",
                        "updated_at": child_folder.updated_at,
                        "deleted_at": child_folder.deleted_at
                    })
        
        if folder.child_resource_ids:
            result = await db.execute(
                select(Resource).where(Resource.id.in_(folder.child_resource_ids)).where(Resource.deleted_at == None)
            )
            child_resources_map = {r.id: r for r in result.scalars().all()}
            
            for resource_id in folder.child_resource_ids:
                resource = child_resources_map.get(resource_id)
                if resource:
                    children.append({
                        "id": resource.id,
                        "name": resource.resource_name,
                        "child_type": "resource",
                        "updated_at": resource.updated_at,
                        "deleted_at": resource.deleted_at,
                        "resource_size": resource.resource_size,
                        "storage_type": resource.storage_type,
                        "resource_status": resource.resource_status
                    })
        
        folder_dict["children"] = children
        
        folder_dict["child_folders"] = folder.child_folder_ids or []
        folder_dict["child_resources"] = folder.child_resource_ids or []
        
        return folder_dict

    @staticmethod
    async def create_folder(db: AsyncSession, folder_data: ResourceFolderCreate) -> Dict[str, Any]:
        validated_data = {
            "folder_name": sanitize_input(folder_data.folder_name),
            "description": folder_data.description,
            "tags": folder_data.tags,
            "child_folder_ids": [],
            "child_resource_ids": []
        }

        if folder_data.parent_id:
            parent_folder = await DatabaseUtils.get_by_id(db, ResourceFolder, folder_data.parent_id)

            if not parent_folder:
                raise ValueError("父文件夹不存在")

            validated_data["parent_id"] = parent_folder.id
            parent_path = parent_folder.path.rstrip('/')
            validated_data["path"] = f"{parent_path}/{parent_folder.folder_name}"
            validated_data["level"] = parent_folder.level + 1

            parent_folder.folder_count += 1

        else:
            validated_data["path"] = "/"
            validated_data["level"] = 0

        validated_data["resource_count"] = 0
        validated_data["direct_resource_count"] = 0
        validated_data["folder_count"] = 0
        validated_data["total_size"] = 0

        folder = ResourceFolder(**validated_data)
        db.add(folder)
        await db.commit()
        await db.refresh(folder)
        
        if folder.parent_id:
            parent_folder = await DatabaseUtils.get_by_id(db, ResourceFolder, folder.parent_id)
            if parent_folder:
                if not parent_folder.child_folder_ids:
                    parent_folder.child_folder_ids = []
                parent_folder.child_folder_ids.append(folder.id)
                flag_modified(parent_folder, "child_folder_ids")
                await db.commit()
        
        try:
            if folder.parent_id is None:
                minio_path = f"{settings.MINIO_BUCKET}/{folder.folder_name}/"
            else:
                folder_path = folder.path.rstrip('/')
                minio_path = f"{settings.MINIO_BUCKET}{folder_path}/{folder.folder_name}/"
            minio_client.upload_bytes(b"", minio_path)
        except Exception as e:
            print(f"在MinIO中创建目录失败: {e}")
        
        return await ResourceFolderService._convert_folder_response(db, folder)

    @staticmethod
    async def update_folder(db: AsyncSession, folder_id: int, folder_data: ResourceFolderUpdate) -> Optional[Dict[str, Any]]:
        folder = await DatabaseUtils.get_by_id(db, ResourceFolder, folder_id)
        if not folder:
            return None
        
        update_data = folder_data.model_dump(exclude_unset=True)
        
        if "folder_name" in update_data:
            update_data["folder_name"] = sanitize_input(update_data["folder_name"])
        
        if "child_folder_ids" in update_data:
            update_data["child_folder_ids"] = [id for id in update_data["child_folder_ids"] if id and is_valid_id(id)]
            
        if "child_resource_ids" in update_data:
            update_data["child_resource_ids"] = [id for id in update_data["child_resource_ids"] if id and is_valid_id(id)]
        
        for field, value in update_data.items():
            setattr(folder, field, value)
        
        await db.commit()
        await db.refresh(folder)
        
        return await ResourceFolderService._convert_folder_response(db, folder)

    @staticmethod
    async def delete_folder(db: AsyncSession, folder_id: int) -> bool:
        folder = await DatabaseUtils.get_by_id(db, ResourceFolder, folder_id)
        if not folder:
            return False
        
        children = await ResourceFolderService.get_folder_children(db, folder_id)
        if children:
            raise ValueError("该文件夹包含子文件夹，无法删除")
        
        from datetime import datetime
        folder.deleted_at = datetime.now()
        
        if folder.parent_id:
            parent_folder = await DatabaseUtils.get_by_id(db, ResourceFolder, folder.parent_id)
            if parent_folder:
                parent_folder.folder_count -= 1
                if parent_folder.child_folder_ids and folder_id in parent_folder.child_folder_ids:
                    parent_folder.child_folder_ids.remove(folder_id)
        
        folder.child_folder_ids = []
        folder.child_resource_ids = []
        
        await db.commit()
        return True