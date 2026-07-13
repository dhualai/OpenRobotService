"""资源模型——再导出 shim（MIGRATION.md 阶段 1）。

真实 ORM 定义已迁至 `app/models/resource.py`。保持
`from app.modules.admin.resource_manager.models.resource import Resource, ResourceType, ...`
等旧导入可用。
"""
from app.models.resource import (
    Resource,
    ResourceType,
    ResourceStatus,
    StorageType,
)

__all__ = ["Resource", "ResourceType", "ResourceStatus", "StorageType"]
