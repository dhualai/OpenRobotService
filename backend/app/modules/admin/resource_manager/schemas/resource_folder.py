from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


class Child(BaseModel):
    id: Optional[int] = Field(None, description="子文件夹或子资源的ID")
    name: Optional[str] = Field(None, description="名称")
    child_type: Optional[str] = Field(default="folder", description="类型（文件夹或资源）", example="resource")
    updated_at: Optional[datetime] = Field(None, description="更新时间")
    deleted_at: Optional[datetime] = Field(None, description="软删除时间")
    resource_size: Optional[int] = Field(None, description="文件大小（字节），仅资源类型有效")
    storage_type: Optional[str] = Field(None, description="存储类型（MINIO/OSS），仅资源类型有效")
    resource_status: Optional[str] = Field(None, description="资源状态，仅资源类型有效")


class ResourceFolderBase(BaseModel):
    folder_name: str = Field(..., description="文件夹名称", max_length=255)
    parent_id: Optional[int] = Field(None, description="父文件夹ID（根文件夹为NULL）")
    description: Optional[str] = Field(None, description="文件夹描述")
    tags: Optional[List[str]] = Field(None, description="标签列表")
    children: Optional[List[Child]] = Field(default_factory=list, description="子文件夹或子资源列表")


class ResourceFolderCreate(ResourceFolderBase):
    pass


class ResourceFolderUpdate(BaseModel):
    folder_name: Optional[str] = Field(None, description="文件夹名称", max_length=255)
    description: Optional[str] = Field(None, description="文件夹描述")
    tags: Optional[List[str]] = Field(None, description="标签列表")
    child_folder_ids: Optional[List[int]] = Field(None, description="子文件夹ID列表")
    child_resource_ids: Optional[List[int]] = Field(None, description="子资源ID列表")


class ResourceFolderResponse(ResourceFolderBase):
    id: int
    path: str
    level: int
    resource_count: int
    direct_resource_count: int
    folder_count: int
    total_size: int
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]
    accessed_at: Optional[datetime]
    
    class Config:
        from_attributes = True