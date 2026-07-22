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
import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager

# Windows GBK → UTF-8：避免 print() 中的 emoji 字符（⏱ 等）导致崩溃
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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

    # 3. 知识库自动检查 & 入库（统一框架，自动发现所有 parser）
    try:
        from qdrant_client import QdrantClient
        from ai.ingestion.registry import discover_parsers, list_registered

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

        # 自动发现所有 parser 模块
        discover_parsers()

        print(f"\n[DEBUG] Qdrant 本地路径: {local}")
        print(f"[DEBUG] 目录存在: {local.is_dir()}")
        try:
            existing = [c.name for c in qdrant.get_collections().collections]
            print(f"[DEBUG] 现有集合 ({len(existing)}): {existing}")
        except Exception:
            print(f"[DEBUG] 无法列出集合")
        print()

        # 关键排序：rebuild=True 的先执行（创建新集合），rebuild=False 的后执行（追加到新集合）
        # 否则 append 模式的 parser 会先创建集合，导致 rebuild parser 误判为"已存在"而跳过
        registered = sorted(
            list_registered(),
            key=lambda m: (m.collection_type, not m.ingester_cls.rebuild),
        )

        for meta in registered:
            ingester = meta.ingester_cls()
            active = ingester.pointer_reader()
            label = meta.description or meta.name

            # 对于 rebuild=True 的 parser：检查活跃集合是否存在
            # 对于 rebuild=False 的 parser（追加模式）：始终运行（idempotent upsert）
            if not ingester.rebuild:
                # 追加模式：始终检查源文件是否更新
                if ingester.validate_source_files():
                    print(f"\n[KB] {label}（追加模式）检查中...")
                    try:
                        await ingester.auto_ingest(client=qdrant)
                    except Exception as e:
                        print(f"[WARN] {label}入库失败: {e}")
            elif not (active and qdrant.collection_exists(active)):
                print(f"\n[KB] {label}知识库未就绪，自动入库中...")
                try:
                    await ingester.auto_ingest(client=qdrant)
                except Exception as e:
                    print(f"[WARN] {label}入库失败: {e}")
                    import traceback
                    traceback.print_exc()
            elif active:
                print(f"[KB] {label}集合: {active}")

    except Exception as e:
        print(f"[WARN] 知识库自动入库失败: {e}")

    # 本地文件模式：释放 lifespan 持有的 QdrantClient，让 RetrievalService 创建自己的
    try:
        qdrant.close()
    except Exception:
        pass

    # 4. 启动诊断后台服务（扫描新工单 → 自动生成 AI 诊断）
    diag_worker = None
    diag_stop = None
    try:
        from ai.agents.AiTaskPlatform.diagnosis_service import diagnosis_worker_start
        diag_worker, diag_stop = diagnosis_worker_start()
        print(f"[OK] Diagnosis worker started (scan interval={get_ai_config().diagnosis_scan_interval}s)")
    except Exception as e:
        print(f"[WARN] Diagnosis worker start failed: {e}")

    print("\n" + "=" * 60)
    print("[OK] Application startup complete")
    print("=" * 60 + "\n")

    yield

    # ── 关闭 ──
    if diag_stop:
        diag_stop.set()
        print("[SHUTDOWN] Diagnosis worker stopping...")
        try:
            await asyncio.wait_for(diag_worker, timeout=10)
        except asyncio.TimeoutError:
            pass


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
from ai.api import qa_router, chat_router, memory_router, assigner_router, task_agent_router
app.include_router(qa_router)
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(assigner_router)
app.include_router(task_agent_router)

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

_cheduan_media_dir = _docs / "cheduan_doc" / "media"
if _cheduan_media_dir.is_dir():
    app.mount(
        f"{_media_prefix}/cheduan_doc",
        StaticFiles(directory=str(_cheduan_media_dir)),
        name="media_cheduan_doc",
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
