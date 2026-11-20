"""资源与资源文件夹 ORM 模型（承 HelpDesk resource_manager）。

原定义于 `app/modules/fqa/resource_manager/models/{resource,resource_folder}.py`，现合并
迁入此处作为唯一定义点（MIGRATION.md 阶段 1）。旧两个路径均改为从本模块再导出。

含 2 张表：resources / resource_folders
"""
import enum

from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SQLEnum, BigInteger, JSON
from sqlalchemy.sql import func

from app.models.base import Base


class ResourceType(str, enum.Enum):
    FILE = "file"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    ARCHIVE = "archive"
    OTHER = "other"


class ResourceStatus(str, enum.Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DELETED = "deleted"


class StorageType(str, enum.Enum):
    MINIO = "MINIO"
    OSS = "OSS"


class Resource(Base):
    __tablename__ = "resources"

    id = Column(BigInteger, primary_key=True, index=True)
    resource_name = Column(String(255), nullable=False, index=True)
    resource_hash_code = Column(String(50), unique=True, nullable=False, index=True)

    owner_id = Column(String(50), nullable=False, index=True)

    resource_type = Column(SQLEnum(ResourceType), nullable=False, default=ResourceType.FILE, index=True)
    resource_status = Column(SQLEnum(ResourceStatus), nullable=False, default=ResourceStatus.UPLOADING, index=True)

    resource_url = Column(String(500), nullable=False)
    resource_format = Column(String(20), nullable=False)
    resource_size = Column(BigInteger, nullable=False, default=0)
    storage_type = Column(SQLEnum(StorageType, native_enum=False), nullable=False, default=StorageType.MINIO, index=True)

    category = Column(String(100), nullable=True, index=True)
    resource_labels = Column(JSON, nullable=True)

    folder_id = Column(BigInteger, nullable=True, index=True)

    view_count = Column(Integer, nullable=False, default=0)
    download_count = Column(Integer, nullable=False, default=0)
    like_count = Column(Integer, nullable=False, default=0)
    favorite_count = Column(Integer, nullable=False, default=0)

    description = Column(Text, nullable=True)
    thumbnail_url = Column(String(500), nullable=True)
    preview_url = Column(String(500), nullable=True)
    extra_metadata = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(DateTime, nullable=True, index=True)
    accessed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Resource(id={self.id}, name='{self.resource_name}', type={self.resource_type})>"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_available(self) -> bool:
        return self.resource_status == ResourceStatus.AVAILABLE and not self.is_deleted

    @property
    def file_size_mb(self) -> float:
        if not self.resource_size:
            return 0.0
        return self.resource_size / 1024 / 1024


class ResourceFolder(Base):
    __tablename__ = "resource_folders"

    id = Column(BigInteger, primary_key=True)
    folder_name = Column(String(255), nullable=False)

    parent_id = Column(BigInteger, nullable=True)
    path = Column(String(500), nullable=False, default="/")
    level = Column(Integer, nullable=False, default=0)
    child_folder_ids = Column(JSON, nullable=True, default=lambda: [])
    child_resource_ids = Column(JSON, nullable=True, default=lambda: [])

    resource_count = Column(Integer, nullable=True, default=0)
    direct_resource_count = Column(Integer, nullable=False, default=0)
    folder_count = Column(Integer, nullable=False, default=0)
    total_size = Column(BigInteger, nullable=False, default=0)

    description = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(DateTime, nullable=True, index=True)
    accessed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<ResourceFolder(id={self.id}, name='{self.folder_name}', path='{self.path}')>"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    @property
    def full_path(self) -> str:
        if self.is_root:
            return f"/{self.folder_name}"
        return f"{self.path}/{self.folder_name}".replace("//", "/")

    @property
    def has_children(self) -> bool:
        return self.folder_count > 0

    @property
    def is_empty(self) -> bool:
        return self.direct_resource_count == 0 and self.folder_count == 0

    @property
    def total_size_mb(self) -> float:
        return round(self.total_size / 1024 / 1024, 2)

    @property
    def total_size_gb(self) -> float:
        return round(self.total_size / 1024 / 1024 / 1024, 2)
