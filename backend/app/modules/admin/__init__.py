"""admin 模块（后台管理）——跨项目看板、项目/风险/日报/授权管理、用户/角色/权限管理。

MIGRATION.md 阶段 3：承接 DAS projects/risks/daily-reports/export + AAS 用户/角色/权限管理。
"""
from fastapi import APIRouter
from app.modules.admin.api.projects import project_router
from app.modules.admin.api.risks import risk_router
from app.modules.admin.api.daily_reports import daily_report_router
from app.modules.admin.api.export import export_router
from app.modules.admin.api.transport_efficiency import transport_efficiency_router
from app.modules.admin.api.users import router as user_router
from app.modules.admin.api.roles import router as role_router
from app.modules.admin.api.permissions import router as permission_router
from app.modules.admin.api.resource_manager import router as resource_manager_router
from app.modules.admin.api.tickets import ticket_router
from app.modules.admin.api.dashboard import dashboard_router

admin_router = APIRouter(prefix="/admin", tags=["admin"])

admin_router.include_router(project_router)
admin_router.include_router(risk_router)
admin_router.include_router(daily_report_router)
admin_router.include_router(export_router)
admin_router.include_router(transport_efficiency_router)
admin_router.include_router(user_router)
admin_router.include_router(role_router)
admin_router.include_router(permission_router)
admin_router.include_router(resource_manager_router)
admin_router.include_router(ticket_router)
admin_router.include_router(dashboard_router)
