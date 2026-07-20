"""
AI 模块 — 独立启动入口

ai/ 位于项目根目录，与 backend/、frontend/ 并列。
完全自举，不依赖 backend 的任何模块。

启动方式：
    cd OpenRobotService
    python ai/run.py
    # → FastAPI 服务运行在 http://0.0.0.0:8401
"""
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# ── 路径初始化 ──────────────────────────────────────────────
_project_root = Path(__file__).resolve().parent.parent  # ai/ → 项目根
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_backend_dir = _project_root / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# 加载 .env（AI 模块独立配置）
from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

# ── FastAPI ──────────────────────────────────────────────────
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时：连通性检查 → Embedding 预热 → 知识库自动入库"""
    print("\n" + "=" * 60)
    print("[STARTUP] Booting AI module...")
    print("=" * 60)

    from ai.config import get_ai_config, validate_ai_config
    from ai.config import (
        get_active_collection, get_active_faq_collection,
        get_active_troubleshooting_collection, get_active_cheduan_collection,
        get_active_translation_collection,
    )

    # 1. 连通性检查（DeepSeek + Qdrant + Redis）
    try:
        results = await validate_ai_config()
        print("\n[OK] External services:")
        for name, info in results.items():
            icon = "[OK]" if info["status"] == "ok" else "[FAIL]"
            print(f"   {icon} {name}: {info['message']}")
    except Exception as e:
        print(f"\n[WARN] Init warning: {e}")

    # 2. Embedding 模型预热
    try:
        from ai.core.embed import get_embed_client
        client = await get_embed_client()
        await client._ensure_model()
        print("[OK] Embedding model pre-warmed")
    except Exception as e:
        print(f"[WARN] Embedding pre-warm failed: {e}")

    # 3. 知识库自动检查 & 入库（5 路并行知识库）
    try:
        from qdrant_client import QdrantClient

        qdrant_cfg = get_ai_config()
        if qdrant_cfg.qdrant_local_path:
            local = Path(qdrant_cfg.qdrant_local_path)
            if not local.is_absolute():
                local = _project_root / local
            qdrant = QdrantClient(path=str(local))
        else:
            qdrant = QdrantClient(
                host=qdrant_cfg.qdrant_host, port=qdrant_cfg.qdrant_port,
                check_compatibility=False,
            )

        kb_registry = [
            ("操作手册", get_active_collection,
             "ai.ingestion.ingest_operation_manual"),
            ("FAQ", get_active_faq_collection,
             "ai.ingestion.ingest_faq"),
            ("问题排查树", get_active_troubleshooting_collection,
             "ai.ingestion.ingest_troubleshooting"),
            ("车端错误码", get_active_cheduan_collection,
             "ai.ingestion.ingest_cheduan"),
            ("翻译表", get_active_translation_collection,
             "ai.ingestion.ingest_translation"),
        ]

        for label, getter, module_name in kb_registry:
            active = getter()
            if not (active and qdrant.collection_exists(active)):
                print(f"\n[KB] {label}知识库未就绪，自动入库中...")
                try:
                    mod = __import__(module_name, fromlist=["auto_ingest"])
                    await mod.auto_ingest()
                except Exception as e:
                    print(f"[WARN] {label}入库失败: {e}")
            elif active:
                print(f"[KB] {label}集合: {active}")

    except Exception as e:
        print(f"[WARN] 知识库自动入库失败: {e}")

    print("\n" + "=" * 60)
    print("[OK] Application startup complete")
    print("=" * 60 + "\n")

    yield


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

# ── 挂载路由（从 ai/api 自举，不再依赖 backend）──────────────
from ai.api import qa_router, chat_router, memory_router, assigner_router
app.include_router(qa_router)
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(assigner_router)

# ── 静态资源（知识库图片等）───────────────────────────────────
from ai.config import get_docs_dir, get_ai_config
_ai_cfg = get_ai_config()
_docs = get_docs_dir()
_media_prefix = _ai_cfg.media_url_prefix

_media_dir = _docs / "operation_doc" / "media"
if _media_dir.is_dir():
    app.mount(
        f"{_media_prefix}/operation_doc",
        StaticFiles(directory=str(_media_dir)),
        name="media_operation_doc",
    )

_faq_media_dir = _docs / "faq_doc" / "media"
if _faq_media_dir.is_dir():
    app.mount(
        f"{_media_prefix}/faq_doc",
        StaticFiles(directory=str(_faq_media_dir)),
        name="media_faq_doc",
    )


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "OpenRobotService AI"}


@app.get("/")
async def root():
    return {
        "service": "OpenRobotService AI 模块",
        "version": "1.0.0",
        "endpoints": {
            "chat": "POST /api/ai/chat",
            "chat_stream": "POST /api/ai/chat/stream",
            "qa_ask": "POST /api/ai/qa/ask",
            "qa_ask_stream": "POST /api/ai/qa/ask/stream",
            "qa_health": "GET /api/ai/qa/health",
            "health": "GET /health",
        },
    }


# ── 入口 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = 8401
    uvicorn.run("ai.run:app", host="0.0.0.0", port=port, reload=True, log_level="info")
