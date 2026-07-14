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
from contextlib import asynccontextmanager

from app.core.database import init_db
from app.ai.config import get_ai_config, validate_ai_config
from app.modules.call.api.diagnosis import qa_router, chat_router, memory_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    print("\n" + "=" * 60)
    print("🚀 启动 AI 模块...")
    print("=" * 60)

    try:
        init_db()
        print("✅ MySQL 表已就绪")
    except Exception as e:
        print(f"⚠️  MySQL 初始化失败: {e}")

    try:
        results = await validate_ai_config()
        print("\n✅ AI 模块外部服务状态：")
        for name, info in results.items():
            status_icon = "✅" if info["status"] == "ok" else "❌"
            print(f"   {status_icon} {name}: {info['message']}")
    except Exception as e:
        print(f"\n⚠️  AI 模块初始化警告: {e}")
        print("   部分功能可能不可用，但 API 仍可启动。")

    # 预热 embedding 模型（后台加载，首次 QA 不用等）
    try:
        from app.ai.core.embed import get_embed_client
        client = await get_embed_client()
        await client._ensure_model()
        print(f"✅ Embedding 模型已预热")
    except Exception as e:
        print(f"⚠️  Embedding 预热失败: {e}")

    print("\n" + "=" * 60)
    print("✅ 应用启动完成")
    print("=" * 60 + "\n")

    yield

    print("\n🛑 关闭应用...")


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
