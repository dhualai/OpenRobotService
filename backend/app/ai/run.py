"""
AI 诊断服务 — 独立启动入口

放在 app/ai/ 下，通过 sys.modules 预注册 app 命名空间，
避免触发 app/__init__.py（含 asyncmy 等 Python 3.14 不可用依赖）。

启动方式：
    cd backend
    python app/ai/run.py
"""
import sys
import types
from pathlib import Path

# 0. 确保 backend 在 sys.path
_backend_dir = Path(__file__).resolve().parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# 1. 阻止 app/__init__.py 及其他非 AI 包的 __init__.py 被加载
#    —— 预先注册最小命名空间包，避免触发 asyncmy 等重量级依赖
_app_ns = types.ModuleType("app")
_app_ns.__path__ = [str(_backend_dir / "app")]
sys.modules["app"] = _app_ns

# 只预注册有问题的中间包：app.modules.call 的 __init__.py 会导入大量非 AI 依赖
# app.modules 的 __init__.py 是空的，无需处理
# app.modules.call.api 不能预注册，否则 Python 找不到 diagnosis.py
_call_ns = types.ModuleType("app.modules.call")
_call_ns.__path__ = [str(_backend_dir / "app" / "modules" / "call")]
sys.modules["app.modules.call"] = _call_ns

# 2. 加载 .env
from dotenv import load_dotenv
load_dotenv(_backend_dir / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n" + "=" * 60)
    print("[STARTUP] Booting AI module...")
    print("=" * 60)

    from app.ai.config import get_ai_config, validate_ai_config, get_active_collection, get_active_faq_collection, get_active_troubleshooting_collection, get_active_cheduan_collection, get_active_translation_collection, get_docs_dir

    try:
        results = await validate_ai_config()
        print("\n[OK] AI module external services:")
        for name, info in results.items():
            status_icon = "[OK]" if info["status"] == "ok" else "[FAIL]"
            print(f"   {status_icon} {name}: {info['message']}")
    except Exception as e:
        print(f"\n[WARN] AI module init warning: {e}")

    try:
        from app.ai.core.embed import get_embed_client
        client = await get_embed_client()
        await client._ensure_model()
        print(f"[OK] Embedding model pre-warmed")
    except Exception as e:
        print(f"[WARN] Embedding pre-warm failed: {e}")

    try:
        from qdrant_client import QdrantClient

        qdrant_cfg = get_ai_config()
        if qdrant_cfg.qdrant_local_path:
            local = Path(qdrant_cfg.qdrant_local_path)
            if not local.is_absolute():
                local = _backend_dir / local
            qdrant = QdrantClient(path=str(local))
        else:
            qdrant = QdrantClient(host=qdrant_cfg.qdrant_host, port=qdrant_cfg.qdrant_port, check_compatibility=False)

        op_active = get_active_collection()
        if not (op_active and qdrant.collection_exists(op_active)):
            print("\n[KB] 操作手册知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_operation_manual import auto_ingest as auto_op
            await auto_op()
        else:
            print(f"[KB] 操作手册集合: {op_active}")

        faq_active = get_active_faq_collection()
        if not (faq_active and qdrant.collection_exists(faq_active)):
            print("\n[KB] FAQ 知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_faq import auto_ingest as auto_faq
            await auto_faq()
        elif faq_active:
            print(f"[KB] FAQ 集合: {faq_active}")

        ts_active = get_active_troubleshooting_collection()
        if not (ts_active and qdrant.collection_exists(ts_active)):
            print("\n[KB] 问题排查树知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_troubleshooting import auto_ingest as auto_ts
            await auto_ts()
        elif ts_active:
            print(f"[KB] 排查树集合: {ts_active}")

        cd_active = get_active_cheduan_collection()
        if not (cd_active and qdrant.collection_exists(cd_active)):
            print("\n[KB] 车端错误码知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_cheduan import auto_ingest as auto_cd
            await auto_cd()
        elif cd_active:
            print(f"[KB] 车端错误码集合: {cd_active}")

        tr_active = get_active_translation_collection()
        if not (tr_active and qdrant.collection_exists(tr_active)):
            print("\n[KB] 翻译表知识库未就绪，自动入库中...")
            from app.ai.ingestion.ingest_translation import auto_ingest as auto_tr
            await auto_tr()
        elif tr_active:
            print(f"[KB] 翻译表集合: {tr_active}")

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

from app.modules.call.api.diagnosis import qa_router, chat_router, memory_router
app.include_router(qa_router)
app.include_router(chat_router)
app.include_router(memory_router)

# 静态资源
from app.ai.config import get_docs_dir, get_ai_config
_ai_cfg = get_ai_config()
_docs = get_docs_dir()
_media_prefix = _ai_cfg.media_url_prefix

_media_dir = _docs / "operation_doc" / "media"
if _media_dir.is_dir():
    app.mount(f"{_media_prefix}/operation_doc", StaticFiles(directory=str(_media_dir)), name="media_operation_doc")

_faq_media_dir = _docs / "faq_doc" / "media"
if _faq_media_dir.is_dir():
    app.mount(f"{_media_prefix}/faq_doc", StaticFiles(directory=str(_faq_media_dir)), name="media_faq_doc")


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
            "health": "GET /health",
        }
    }


if __name__ == "__main__":
    import uvicorn
    port = 8400
    uvicorn.run("app.ai.run:app", host="0.0.0.0", port=port, reload=True, log_level="info")
