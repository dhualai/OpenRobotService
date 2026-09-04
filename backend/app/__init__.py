from dotenv import load_dotenv
load_dotenv()  # 把 .env 注入 os.environ

from app.services.logging import setup_logging
setup_logging()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
import time
from app.core.config import settings
from app.core.database import init_users_db
from app.core.auth_routes import router as auth_router
from app.wechat import wechat_api_router
from app.modules.admin import admin_router
from app.modules.tasks import tasks_router
from app.modules.call import call_router

import app.integrations  # noqa: E402  装载外部任务源插件（按 TASK_SOURCES_ENABLED 自注册）
from app.integrations.api import router as integrations_sources_router
from app.integrations.mappings_api import router as integrations_mappings_router
from app.wechat.services.user_info_snapshot import (
    start_user_info_snapshot_scheduler,
    stop_user_info_snapshot_scheduler,
)
from app.wechat.services.user_statistics_snapshot import (
    start_user_statistics_scheduler,
    stop_user_statistics_scheduler,
)
security = HTTPBearer()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters={
        "defaultModelsExpandDepth": 2,
        "defaultModelExpandDepth": 2,
        "defaultModelRendering": "model",
        "displayRequestDuration": True,
        "docExpansion": "none",
        "filter": True,
        "showExtensions": True
    }
)

app.openapi_schema = None

@app.on_event("startup")
async def startup_event():
    if app.openapi_schema is None:
        app.openapi_schema = app.openapi()
    app.openapi_schema["components"] = app.openapi_schema.get("components", {})
    app.openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    app.openapi_schema["security"] = [{"BearerAuth": []}]
    
    init_users_db()

    # 确保对象存储 bucket 存在（MinIO 未启动仅告警，不阻塞启动）
    try:
        from app.utils.minio_client import ensure_minio_buckets
        ensure_minio_buckets()
    except Exception as e:  # noqa: BLE001
        print(f"[MinIO] 初始化 bucket 失败: {e}")

    # 整点拉取 batch-user-info 存入 user_info 表（每天仅保留最新一条快照）
    start_user_info_snapshot_scheduler()

    # 每个整点拉取昨日用户增减数据，并覆盖为该日期最新渠道明细
    start_user_statistics_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    stop_user_info_snapshot_scheduler()
    stop_user_statistics_scheduler()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=f"{settings.API_V1_STR}/auth", tags=["认证"])
app.include_router(admin_router, prefix=f"{settings.API_V1_STR}")
# integrations 任务源路由须在 tasks_router 之前注册：避免 GET /tasks/sources 被
# tasks 模块的 GET /tasks/{task_id}（贪婪路径参数）抢先吞掉
app.include_router(integrations_sources_router, prefix=f"{settings.API_V1_STR}")
app.include_router(tasks_router, prefix=f"{settings.API_V1_STR}")
app.include_router(call_router, prefix=f"{settings.API_V1_STR}")
app.include_router(wechat_api_router, prefix=f"{settings.API_V1_STR}")
app.include_router(integrations_mappings_router, prefix=f"{settings.API_V1_STR}/admin")

@app.get(f"{settings.API_V1_STR}/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    }
