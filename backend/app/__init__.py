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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=f"{settings.API_V1_STR}/auth", tags=["认证"])
app.include_router(admin_router, prefix=f"{settings.API_V1_STR}")
app.include_router(tasks_router, prefix=f"{settings.API_V1_STR}")
app.include_router(call_router, prefix=f"{settings.API_V1_STR}")
app.include_router(wechat_api_router, prefix=f"{settings.API_V1_STR}")

@app.get(f"{settings.API_V1_STR}/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    }