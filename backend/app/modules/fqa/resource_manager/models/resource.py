from typing import Optional
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SQLEnum, BigInteger, JSON
from sqlalchemy.sql import func
import enum
from app.core.database import Base


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