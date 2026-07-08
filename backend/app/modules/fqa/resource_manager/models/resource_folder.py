from typing import Optional
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger, JSON
from sqlalchemy.sql import func
from app.core.database import Base


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