from fastapi import APIRouter
from app.modules.das.api.data import router as data_router
from app.modules.das.api.risks import risk_router
from app.modules.das.api.notify import router as notify_router
from app.modules.das.api.daily_reports import daily_report_router
from app.modules.das.api.projects import project_router
from app.modules.das.api.export import export_router

api_router = APIRouter(prefix="/api")

api_router.include_router(data_router)
api_router.include_router(risk_router)
api_router.include_router(notify_router)
api_router.include_router(daily_report_router)
api_router.include_router(project_router)
api_router.include_router(export_router)