"""
AI 模块配置

所有配置的来源：
    backend/.env  ← 唯一配置入口，通过 load_dotenv() 注入环境变量

运行时读取：
    from app.ai.config import get_ai_config
    config = get_ai_config()
    print(config.deepseek_api_key)

知识库热更新：
    集合名通过 app/kb/active_collection.txt 指针文件动态切换，
    入库脚本写入新集合后更新指针，服务无需重启。
"""
import os
from pathlib import Path
from functools import lru_cache
from pydantic import BaseModel, Field

# 指针文件：记录当前活跃的 Qdrant 集合名
_ACTIVE_COLLECTION_POINTER = Path(__file__).resolve().parent.parent.parent / "app" / "kb" / "active_collection.txt"
_ACTIVE_FAQ_COLLECTION_POINTER = Path(__file__).resolve().parent.parent.parent / "app" / "kb" / "active_faq_collection.txt"


class AIConfig(BaseModel):
    """AI 模块配置（值全部来自环境变量，即 .env）"""

    # ========== DeepSeek LLM ==========
    deepseek_api_key: str = Field(default="", description="DeepSeek API Key")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", description="API 地址")
    deepseek_model: str = Field(default="deepseek-v4-flash", description="模型名")
    llm_connect_timeout: float = Field(default=3.0)
    llm_read_timeout: float = Field(default=30.0)  # Agent 回复可能较长
    llm_cache_ttl: int = Field(default=300)

    # ========== Qdrant 向量库 ==========
    qdrant_host: str = Field(default="localhost")
    qdrant_port: int = Field(default=6333)
    qdrant_collection_name: str = Field(default="operation_docs")
    qdrant_timeout: float = Field(default=5.0)
    qdrant_local_path: str = Field(default="", description="本地模式路径，非空时忽略 host/port")

    # ========== Redis ==========
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_context_turns: int = Field(default=3)
    redis_ttl: int = Field(default=0, description="0=永久，>0=秒")

    # ========== Embedding ==========
    embedding_model_name: str = Field(default="BAAI/bge-small-zh-v1.5")
    embedding_device: str = Field(default="cpu")
    embedding_batch_size: int = Field(default=32)
    embedding_cache_size: int = Field(default=10000)

    # ========== 检索 ==========
    retrieval_top_k: int = Field(default=3)
    retrieval_score_threshold: float = Field(default=0.65)

    # ========== 派单 ==========
    dispatch_api_url: str = Field(default="", description="派单系统推送地址")
    upload_dir: str = Field(default="./uploads", description="附件上传目录")

    # ========== 超时 ==========
    ai_chain_timeout: float = Field(default=2.5)

    # ========== 文档路径 ==========
    docs_path: str = Field(default="", description="原始文档根目录，默认 ../docs/（相对于 backend/）")


def get_active_collection() -> str:
    """
    读取当前活跃的 Qdrant 集合名。
    优先读指针文件（入库脚本写入），文件不存在时回退到 .env。
    此函数不缓存，每次调用都读文件，保证热更新。
    """
    try:
        if _ACTIVE_COLLECTION_POINTER.exists():
            name = _ACTIVE_COLLECTION_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return os.getenv("QDRANT_COLLECTION", "operation_docs")


def _write_active_collection(name: str) -> None:
    """写入活跃集合指针（入库脚本调用）"""
    _ACTIVE_COLLECTION_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_COLLECTION_POINTER.write_text(name, encoding="utf-8")


def get_active_faq_collection() -> str:
    """读取当前活跃的 FAQ Qdrant 集合名"""
    try:
        if _ACTIVE_FAQ_COLLECTION_POINTER.exists():
            name = _ACTIVE_FAQ_COLLECTION_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_faq_collection(name: str) -> None:
    """写入活跃 FAQ 集合指针（入库脚本调用）"""
    _ACTIVE_FAQ_COLLECTION_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_FAQ_COLLECTION_POINTER.write_text(name, encoding="utf-8")


def get_docs_dir() -> Path:
    """
    获取文档根目录。
    优先读 DOCS_PATH 环境变量，未设置时默认 backend/../docs/
    """
    config = get_ai_config()
    if config.docs_path:
        p = Path(config.docs_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent.parent / p
        return p
    return Path(__file__).resolve().parent.parent.parent.parent / "docs"


@lru_cache()
def get_ai_config() -> AIConfig:
    """
    获取 AI 配置单例
    所有值从环境变量读取（由 backend/.env 注入）
    注意：qdrant_collection_name 可能被指针文件覆盖（见 get_active_collection）
    """
    return AIConfig(
        # DeepSeek
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        llm_connect_timeout=float(os.getenv("LLM_CONNECT_TIMEOUT", "3.0")),
        llm_read_timeout=float(os.getenv("LLM_READ_TIMEOUT", "30.0")),
        llm_cache_ttl=int(os.getenv("LLM_CACHE_TTL", "300")),
        # Qdrant
        qdrant_host=os.getenv("QDRANT_HOST", "localhost"),
        qdrant_port=int(os.getenv("QDRANT_PORT", "6333")),
        qdrant_collection_name=os.getenv("QDRANT_COLLECTION", "operation_docs"),
        qdrant_timeout=float(os.getenv("QDRANT_TIMEOUT", "5.0")),
        qdrant_local_path=os.getenv("QDRANT_LOCAL_PATH", ""),
        # Redis
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_max_context_turns=int(os.getenv("REDIS_MAX_CONTEXT_TURNS", "3")),
        redis_ttl=int(os.getenv("REDIS_TTL", "0")),
        # Embedding
        embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5"),
        embedding_device=os.getenv("EMBEDDING_DEVICE", "cpu"),
        embedding_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")),
        embedding_cache_size=int(os.getenv("EMBEDDING_CACHE_SIZE", "10000")),
        # 检索
        retrieval_top_k=int(os.getenv("RETRIEVAL_TOP_K", "3")),
        retrieval_score_threshold=float(os.getenv("RETRIEVAL_SCORE_THRESHOLD", "0.65")),
        # 超时
        ai_chain_timeout=float(os.getenv("AI_CHAIN_TIMEOUT", "2.5")),
        # 派单
        dispatch_api_url=os.getenv("DISPATCH_API_URL", ""),
        upload_dir=os.getenv("UPLOAD_DIR", "./uploads"),
        # 文档路径
        docs_path=os.getenv("DOCS_PATH", ""),
    )


async def validate_ai_config() -> dict:
    """
    启动时连通性检查（失败不阻止启动）
    """
    from app.ai.core import get_llm_client, QdrantClientWrapper, get_memory_manager

    results = {
        "deepseek": {"status": "pending", "message": ""},
        "qdrant": {"status": "pending", "message": ""},
        "redis": {"status": "pending", "message": ""},
    }

    try:
        llm = await get_llm_client()
        await llm.complete("你好", max_tokens=5)
        results["deepseek"] = {"status": "ok", "message": "连接成功"}
    except Exception as e:
        results["deepseek"] = {"status": "error", "message": str(e)}

    try:
        qdrant = await QdrantClientWrapper.from_config()
        collections = await qdrant.list_collections()
        results["qdrant"] = {"status": "ok", "message": f"已连接，集合: {collections}"}
    except Exception as e:
        results["qdrant"] = {"status": "error", "message": str(e)}

    try:
        redis_mgr = await get_memory_manager()
        await redis_mgr.health_check()
        results["redis"] = {"status": "ok", "message": "连接成功"}
    except Exception as e:
        results["redis"] = {"status": "error", "message": str(e)}

    return results
