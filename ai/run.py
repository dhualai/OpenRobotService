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
    """启动时：日志初始化 → 连通性检查 → Embedding 预热 → 知识库自动入库"""
    from ai.core.logging import setup_logging, get_logger
    setup_logging()
    logger = get_logger(__name__)
    logger.info("=" * 40)
    logger.info("AI 模块启动中...")

    from ai.config import get_ai_config, validate_ai_config

    # 1. 连通性检查（DeepSeek + Qdrant + Redis）
    try:
        results = await validate_ai_config()
        for name, info in results.items():
            if info["status"] == "ok":
                logger.info(f"外部服务连通: {name} — {info['message']}")
            else:
                logger.error(f"外部服务不通: {name} — {info['message']}")
    except Exception as e:
        logger.error(f"连通性检查异常: {e}", exc_info=True)

    # 2. Embedding 模型预热
    try:
        from ai.core.embed import get_embed_client
        client = await get_embed_client()
        await client._ensure_model()
        logger.info("Embedding 模型预热完成")
    except Exception as e:
        logger.error(f"Embedding 模型预热失败: {e}", exc_info=True)

    # 3. 知识库自动检查 & 入库（统一框架，自动发现所有 parser）
    qdrant = None
    local = None
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

        discover_parsers()
        logger.debug(f"Qdrant 本地路径: {local}")

        try:
            existing = [c.name for c in qdrant.get_collections().collections]
            logger.debug(f"现有 Qdrant 集合 ({len(existing)}): {existing}")
        except Exception:
            logger.debug("无法列出 Qdrant 集合")

        registered = sorted(
            list_registered(),
            key=lambda m: (m.collection_type, not m.ingester_cls.rebuild),
        )

        for meta in registered:
            ingester = meta.ingester_cls()
            ingester.verbose = False  # 静默模式，状态由 logger 输出
            active = ingester.pointer_reader()
            label = meta.description or meta.name

            if not ingester.rebuild:
                if ingester.validate_source_files():
                    try:
                        await ingester.auto_ingest(client=qdrant)
                    except Exception as e:
                        logger.error(f"知识库 {label} 入库失败: {e}", exc_info=True)
            elif not (active and qdrant.collection_exists(active)):
                logger.info(f"知识库 {label} 未就绪，自动入库中...")
                try:
                    await ingester.auto_ingest(client=qdrant)
                except Exception as e:
                    logger.error(f"知识库 {label} 入库失败: {e}", exc_info=True)
            elif active:
                logger.info(f"知识库 {label} 已就绪，集合: {active}")

    except Exception as e:
        logger.error(f"知识库自动入库异常: {e}", exc_info=True)

    if qdrant is not None:
        try:
            qdrant.close()
        except Exception:
            pass

    # 4. 启动诊断后台服务
    diag_worker = None
    diag_stop = None
    try:
        from ai.agents.AiTaskPlatform.services.diagnosis_worker import diagnosis_worker_start
        diag_worker, diag_stop = diagnosis_worker_start()
        logger.info(f"诊断后台服务已启动 (scan interval={get_ai_config().diagnosis_scan_interval}s)")
    except Exception as e:
        logger.error(f"诊断后台服务启动失败: {e}", exc_info=True)

    # 5. 启动派单后台 Worker（定时扫描待派单池 → 自动指派）
    assign_worker = None
    assign_worker_task = None
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.worker import AssignmentWorker
        _cfg = get_ai_config()
        assign_worker = AssignmentWorker(interval=_cfg.assign_scan_interval)
        assign_worker_task = asyncio.create_task(assign_worker.run())
        assign_worker._task = assign_worker_task
        logger.info(f"派单 Worker 已启动 (scan interval={_cfg.assign_scan_interval}s)")
    except Exception as e:
        logger.error(f"派单 Worker 启动失败: {e}", exc_info=True)

    logger.info("AI 模块启动完成")

    yield

    # ── 关闭 ──
    if assign_worker:
        await assign_worker.stop()
    if diag_stop:
        diag_stop.set()
        logger.info("诊断后台服务停止中...")
        try:
            await asyncio.wait_for(diag_worker, timeout=10)
        except asyncio.TimeoutError:
            logger.warning("诊断后台服务停止超时(10s)")


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
from ai.api import qa_router, chat_router, memory_router, task_agent_router
app.include_router(qa_router)
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(task_agent_router)

# ── 挂载 AiDataAnalysisPlatform 路由（数据分析）──────────────
from ai.agents.AiDataAnalysisPlatform.router import router as analysis_router
app.include_router(analysis_router, prefix="/api/ai/analysis", tags=["AI数据分析"])

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
