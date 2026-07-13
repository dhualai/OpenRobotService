from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.modules.admin.resource_manager.models.resource import ResourceType, ResourceStatus
import json


class ResourceBase(BaseModel):
    resource_name: str = Field(..., description="资源名称", max_length=255)
    resource_hash_code: str = Field(..., description="资源唯一编码", max_length=50)
    owner_id: str = Field(..., description="所有者ID", max_length=50)
    resource_type: ResourceType = Field(..., description="资源类型")
    resource_status: ResourceStatus = Field(default=ResourceStatus.UPLOADING, description="资源状态")
    resource_url: str = Field(..., description="资源URL/存储路径", max_length=500)
    resource_format: str = Field(..., description="文件格式/扩展名", max_length=20)
    resource_size: int = Field(default=0, description="文件大小（字节）", ge=0)
    category: Optional[str] = Field(None, description="资源分类", max_length=100)
    resource_labels: Optional[List[str]] = Field(default_factory=list, description="标签列表")
    description: Optional[str] = Field(None, description="资源描述")
    thumbnail_url: Optional[str] = Field(None, description="缩略图URL", max_length=500)
    preview_url: Optional[str] = Field(None, description="预览URL", max_length=500)
    extra_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="扩展元数据")

    @field_validator('extra_metadata', mode='before')
    @classmethod
    def parse_extra_metadata(cls, v: Any) -> Optional[Dict[str, Any]]:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return {}

    class Config:
        from_attributes = True
        populate_by_name = True


class ResourceCreate(BaseModel):
    owner_id: str = Field(..., description="所有者ID", max_length=50)
    resource_type: ResourceType = Field(..., description="资源类型")
    category: Optional[str] = Field(None, description="资源分类", max_length=100)
    resource_labels: Optional[List[str]] = Field(default_factory=list, description="标签列表")
    description: Optional[str] = Field(None, description="资源描述")
    
    class Config:
        from_attributes = True
        populate_by_name = True


class ResourceUpdate(BaseModel):
    resource_name: Optional[str] = Field(None, description="资源名称", max_length=255)
    resource_status: Optional[ResourceStatus] = Field(None, description="资源状态")
    category: Optional[str] = Field(None, description="资源分类", max_length=100)
    resource_labels: Optional[List[str]] = Field(None, description="标签列表")
    description: Optional[str] = Field(None, description="资源描述")
    thumbnail_url: Optional[str] = Field(None, description="缩略图URL", max_length=500)
    preview_url: Optional[str] = Field(None, description="预览URL", max_length=500)
    folder_id: Optional[int] = Field(None, description="所属文件夹ID")
    extra_metadata: Optional[Dict[str, Any]] = Field(None, description="扩展元数据")

    @field_validator('extra_metadata', mode='before')
    @classmethod
    def parse_extra_metadata(cls, v: Any) -> Optional[Dict[str, Any]]:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return {}

    class Config:
        from_attributes = True
        populate_by_name = True


class ResourceResponse(ResourceBase):
    id: int
    view_count: int
    download_count: int
    like_count: int
    favorite_count: int
    folder_id: Optional[int] = Field(None, description="所属文件夹ID")
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]
    accessed_at: Optional[datetime]
    
    class Config:
        from_attributes = True
        populate_by_name = True


class ResourceStats(BaseModel):
    total_resources: int = Field(..., description="总资源数")
    available_resources: int = Field(..., description="可用资源数")
    total_size: int = Field(..., description="总大小")
    type_distribution: Dict[ResourceType, int] = Field(..., description="资源类型分布")


class SyncBuildDeployRequest(BaseModel):
    execute_nginx_reload: bool = Field(False, description="是否执行nginx重载")