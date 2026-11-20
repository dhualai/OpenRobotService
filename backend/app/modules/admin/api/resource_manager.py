"""资源管理路由——从 fqa/resource_manager 迁移而来。"""
from fastapi import APIRouter
from app.modules.admin.resource_manager.api import resource, resource_folder, minio

router = APIRouter(prefix="/resource-manager", tags=["resource-manager"])

router.include_router(resource.router)
router.include_router(resource_folder.router)
router.include_router(minio.router)