from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
import time
from app.core.config import settings
from app.core.database import init_users_db
from app.modules.das.utils.database_init import init_das_db
from app.modules.fqa.utils.database_init import init_fqa_db
from app.modules.aas import aas_router
from app.modules.das.api.routes import api_router as das_api_router
from app.modules.wechat import wechat_api_router
from app.modules.fqa import fqa_router

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
    init_das_db()
    init_fqa_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(aas_router, prefix=f"{settings.API_V1_STR}/AAS")
app.include_router(das_api_router, prefix=f"{settings.API_V1_STR}/DAS")
app.include_router(wechat_api_router, prefix=f"{settings.API_V1_STR}")
app.include_router(fqa_router, prefix=f"{settings.API_V1_STR}")

@app.get(f"{settings.API_V1_STR}/AAS/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    }