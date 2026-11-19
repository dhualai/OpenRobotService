from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.drafts import router as drafts_router
from app.doc_updates import doc_update_closed, migrated_notice, migrated_url
from app.infra.db import configure
from app.infra.models import Base
from app.settings import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    configure(s.database_url)
    from app.infra import db as dbmod
    from app.infra.schema import apply_schema
    from app.services.bootstrap import apply_directory, upsert_dev_admin

    assert dbmod.engine is not None and dbmod.SessionLocal is not None
    try:
        apply_schema(dbmod.engine)
    except Exception:
        import logging

        logging.getLogger("topo.schema").exception("alembic upgrade failed; create_all fallback")
        Base.metadata.create_all(dbmod.engine)
    db = dbmod.SessionLocal()
    try:
        apply_directory(db)
        upsert_dev_admin(db, s)
        from app.services.audit import purge_expired
        from app.services.doc_views import purge_visits

        purge_expired(db)
        purge_visits(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    configure(s.database_url)
    app = FastAPI(
        title="topo-portal",
        docs_url="/docs" if s.openapi_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if s.openapi_enabled else None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(drafts_router, prefix="/api/v1")

    @app.middleware("http")
    async def freeze_doc_updates(request: Request, call_next):
        if doc_update_closed(request.method, request.url.path):
            return JSONResponse({"detail": migrated_notice()}, status_code=403)
        return await call_next(request)

    @app.get("/api/v1/health")
    def health():
        return {"ok": True, "migrated_url": migrated_url()}

    return app


app = create_app()
