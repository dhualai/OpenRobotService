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
load_dotenv(_backend_dir / "app" / "ai" / ".env")

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

# 静态资源：操作手册图片（知识库引用的 media 文件）
_media_dir = _backend_dir.parent / "docs" / "operation_doc" / "media"
if _media_dir.is_dir():
    app.mount(
        "/api/media/operation_doc",
        StaticFiles(directory=str(_media_dir)),
        name="media_operation_doc",
    )
    print(f"[OK] Media static: {_media_dir} ({len(list(_media_dir.iterdir()))} files)")
else:
    print(f"[WARN] Media dir not found: {_media_dir}")


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

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
