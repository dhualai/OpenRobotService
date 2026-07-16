"""
FastAPI 应用启动入口

运行方式：
    cd backend
    python -m app.main

或使用 uvicorn：
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path 中
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# 加载 .env（必须在其他 app 导入之前）
from dotenv import load_dotenv
load_dotenv(_backend_dir / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.database import init_db
from app.ai.config import get_ai_config, validate_ai_config
from app.modules.call.api.diagnosis import qa_router, chat_router, memory_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    print("\n" + "=" * 60)
    print("[STARTUP] Booting AI module...")
    print("=" * 60)

    try:
        init_db()
        print("[OK] MySQL tables ready")
    except Exception as e:
        print(f"[WARN] MySQL init failed: {e}")

    try:
        results = await validate_ai_config()
        print("\n[OK] AI module external services:")
        for name, info in results.items():
            status_icon = "[OK]" if info["status"] == "ok" else "[FAIL]"
            print(f"   {status_icon} {name}: {info['message']}")
    except Exception as e:
        print(f"\n[WARN] AI module init warning: {e}")
        print("   Some features may be unavailable, but API will start.")

    # Pre-warm embedding model
    try:
        from app.ai.core.embed import get_embed_client
        client = await get_embed_client()
        await client._ensure_model()
        print(f"[OK] Embedding model pre-warmed")
    except Exception as e:
        print(f"[WARN] Embedding pre-warm failed: {e}")

    # 知识库自动检查：首次启动无 collection 时自动入库
    try:
        from app.ai.config import get_active_collection, get_active_faq_collection, get_active_troubleshooting_collection, get_docs_dir
        from qdrant_client import QdrantClient

        qdrant_cfg = get_ai_config()
        if qdrant_cfg.qdrant_local_path:
            local = Path(qdrant_cfg.qdrant_local_path)
            if not local.is_absolute():
                local = _backend_dir / local
            qdrant = QdrantClient(path=str(local))
        else:
            qdrant = QdrantClient(host=qdrant_cfg.qdrant_host, port=qdrant_cfg.qdrant_port)

        op_active = get_active_collection()
        op_exists = op_active and qdrant.collection_exists(op_active)
        if not op_exists:
            print("\n[KB] 操作手册知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_operation_manual import auto_ingest as auto_op
            await auto_op()
        else:
            print(f"[KB] 操作手册集合: {op_active}")

        faq_active = get_active_faq_collection()
        faq_exists = faq_active and qdrant.collection_exists(faq_active)
        if not faq_exists:
            print("\n[KB] FAQ 知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_faq import auto_ingest as auto_faq
            await auto_faq()
        elif faq_active:
            print(f"[KB] FAQ 集合: {faq_active}")

        ts_active = get_active_troubleshooting_collection()
        ts_exists = ts_active and qdrant.collection_exists(ts_active)
        if not ts_exists:
            print("\n[KB] 问题排查树知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_troubleshooting import auto_ingest as auto_ts
            await auto_ts()
        elif ts_active:
            print(f"[KB] 排查树集合: {ts_active}")

    except Exception as e:
        print(f"[WARN] 知识库自动入库失败: {e}")

    print("\n" + "=" * 60)
    print("[OK] Application startup complete")
    print("=" * 60 + "\n")

    yield

    print("\n[SHUTDOWN] Stopping application...")


app = FastAPI(
    title="OpenRobotService AI 模块",
    description="操作问答引导 · 知识库检索 · AI 助手",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(qa_router)
app.include_router(chat_router)
app.include_router(memory_router)

# 静态资源：操作手册 + FAQ 图片（通过 DOCS_PATH + MEDIA_URL_PREFIX 定位）
from app.ai.config import get_docs_dir as _get_docs_dir, get_ai_config as _get_ai_config
_ai_cfg = _get_ai_config()
_docs = _get_docs_dir()
_media_prefix = _ai_cfg.media_url_prefix  # 默认 /api/media，可通过 .env 覆盖

_media_dir = _docs / "operation_doc" / "media"
if _media_dir.is_dir():
    _op_media_url = f"{_media_prefix}/operation_doc"
    app.mount(
        _op_media_url,
        StaticFiles(directory=str(_media_dir)),
        name="media_operation_doc",
    )
    # Media mount silent
else:
    print(f"[WARN] Media dir not found: {_media_dir}")

_faq_media_dir = _docs / "faq_doc" / "media"
if _faq_media_dir.is_dir():
    _faq_media_url = f"{_media_prefix}/faq_doc"
    app.mount(
        _faq_media_url,
        StaticFiles(directory=str(_faq_media_dir)),
        name="media_faq_doc",
    )  # silent


@app.get("/health", tags=["系统"])
async def health_check():
    return {"status": "ok", "service": "OpenRobotService AI"}


@app.get("/", tags=["系统"])
async def root():
    return {
        "service": "OpenRobotService AI 模块",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "chat": "POST /api/ai/chat",
            "chat_stream": "POST /api/ai/chat/stream",
            "qa_ask": "POST /api/ai/qa/ask",
            "qa_ask_stream": "POST /api/ai/qa/ask/stream",
            "qa_submit": "POST /api/ai/qa/submit",
            "qa_health": "GET /api/ai/qa/health",
            "memory_history": "GET /api/ai/memory/history",
            "memory_clear": "DELETE /api/ai/memory/clear",
            "health": "GET /health",
        }
    }


if __name__ == "__main__":
    import uvicorn

    port = 8400
    print("服务启动...")
    print(f"服务地址: http://0.0.0.0:{port}")
    print(f"API文档: http://0.0.0.0:{port}/docs")
    print("默认管理员账号: admin / 123456")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
    )
