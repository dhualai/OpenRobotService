"""
AI 模块配置

所有配置的来源：
    ai/.env  ← 独立配置，通过 load_dotenv() 注入环境变量

运行时读取：
    from ai.config import get_ai_config
    config = get_ai_config()
    print(config.deepseek_api_key)

知识库热更新：
    集合名通过 ai/kb/active_collection.txt 指针文件动态切换，
    入库脚本写入新集合后更新指针，服务无需重启。
"""
import os
from pathlib import Path
from functools import lru_cache
from pydantic import BaseModel, Field

# .md 源文件目录（OpenRobotService_Data/kb/）
_KB_DIR = (Path(__file__).resolve().parent.parent.parent / "OpenRobotService_Data" / "kb").resolve()

# 活跃集合指针目录（ai/kb/）
_POINTER_DIR = (Path(__file__).resolve().parent / "kb").resolve()

# 五层 domain 架构：industry / company / team / project / personal
KB_DOMAINS = ["industry", "company", "team", "project", "personal"]

_KB_POINTERS = {
    d: _POINTER_DIR / f"active_{d}_collection.txt"
    for d in KB_DOMAINS
}


class AIConfig(BaseModel):
    """AI 模块配置（值全部来自环境变量，即 .env）"""

    # ========== DeepSeek LLM ==========
    deepseek_api_key: str = Field(default="", description="DeepSeek API Key")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", description="API 地址")
    deepseek_model: str = Field(default="deepseek-v4-flash", description="模型名")
    llm_connect_timeout: float = Field(default=3.0)
    llm_read_timeout: float = Field(default=30.0)  # Agent 回复可能较长

    # ========== Vision LLM（图片分析，OpenAI 兼容接口）==========
    vision_api_key: str = Field(default="", description="视觉 API Key")
    vision_base_url: str = Field(default="", description="视觉 API 地址")
    vision_model: str = Field(default="gpt-4o", description="视觉模型名")

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
    assign_scan_interval: int = Field(default=120, description="派单 Worker 兜底扫描间隔（秒），Pub/Sub 事件触发已覆盖主路径")
    upload_dir: str = Field(default="./uploads", description="附件上传目录")

    # ========== 诊断服务 ==========
    diagnosis_scan_interval: int = Field(default=60, description="诊断服务扫描新工单间隔（秒）")

    # ========== MinIO 对象存储（附件图片读取）==========
    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_bucket: str = Field(default="helpdesk")
    minio_secure: bool = Field(default=False)
    minio_api_prefix: str = Field(default="", description="MinIO 反向代理路径前缀（如 /minio），为空则直连")

    # ========== 企业微信 ==========
    wecom_corpid: str = Field(default="", description="企业微信 企业ID")
    wecom_corpsecret: str = Field(default="", description="企业微信 应用Secret")
    wecom_docid: str = Field(default="", description="企业微信 Smartsheet 文档ID")
    wecom_sheet_id: str = Field(default="", description="企业微信 Smartsheet 子表ID")

    # ========== Meilisearch 全文检索（项目匹配）==========
    meili_enabled: bool = Field(default=True, description="是否启用 Meilisearch 项目匹配")
    meili_host_url: str = Field(default="http://localhost:7700")
    meili_master_key: str = Field(default="", description="开发模式为空字符串时跳过 Authorization 头")

    # ========== Debug ==========
    debug_assign_to_admin: bool = Field(default=False, description="开发模式：所有工单直接分配给 admin，跳过 AI 派单")
    # ========== 超时 ==========
    ai_chain_timeout: float = Field(default=2.5)

    # ========== 文档路径 ==========
    docs_path: str = Field(default="", description="原始文档根目录，默认 ai/docs/")

    # ========== CodeSkill 代码检索 ==========
    code_skill_paths: str = Field(default="", description="代码索引根目录，逗号分隔")
    media_url_prefix: str = Field(default="/api/ai/media", description="媒体文件 URL 前缀，用于前端渲染图片")


# ── Domain-based active collection pointers ─────────────────────

def get_active_collection_for(domain: str) -> str:
    """读取指定 domain 的活跃 Qdrant 集合名"""
    try:
        p = _KB_POINTERS.get(domain)
        if p and p.exists():
            name = p.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def write_active_collection_for(domain: str, name: str) -> None:
    """写入指定 domain 的活跃集合指针（入库脚本调用）"""
    p = _KB_POINTERS.get(domain)
    if p:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(name, encoding="utf-8")


# ── 向后兼容别名（retrieval.py / pipeline.py / task agent 都在用）──

def get_active_collection() -> str:
    """向后兼容：读取 team domain 的活跃集合名"""
    return get_active_collection_for("team") or os.getenv("QDRANT_COLLECTION", "operation_docs")


def get_active_faq_collection() -> str:
    return get_active_collection_for("team")


def get_active_platform_faq_collection() -> str:
    return get_active_collection_for("team")


def get_active_cheduan_collection() -> str:
    return get_active_collection_for("company")


def get_active_translation_collection() -> str:
    return get_active_collection_for("team")


def get_active_usp_diagnosis_collection() -> str:
    return get_active_collection_for("team")


def get_active_troubleshooting_collection() -> str:
    return get_active_collection_for("team")


def get_active_cheduan_manual_collection() -> str:
    return get_active_collection_for("company")


def get_active_task_resolutions_collection() -> str:
    return get_active_collection_for("project")


# writer 别名（lambda 实现，避免重复 try/except 逻辑）
_write_active_collection = lambda n: write_active_collection_for("team", n)
_write_active_faq_collection = lambda n: write_active_collection_for("team", n)
_write_active_platform_faq_collection = lambda n: write_active_collection_for("team", n)
_write_active_cheduan_collection = lambda n: write_active_collection_for("company", n)
_write_active_translation_collection = lambda n: write_active_collection_for("team", n)
_write_active_usp_diagnosis_collection = lambda n: write_active_collection_for("team", n)
_write_active_troubleshooting_collection = lambda n: write_active_collection_for("team", n)
_write_active_cheduan_manual_collection = lambda n: write_active_collection_for("company", n)
_write_active_task_resolutions_collection = lambda n: write_active_collection_for("project", n)


def get_docs_dir() -> Path:
    """
    获取文档根目录（知识库源文件所在目录）。
    优先读 DOCS_PATH 环境变量，未设置时默认 ai/docs/
    """
    config = get_ai_config()
    if config.docs_path:
        p = Path(config.docs_path)
        if not p.is_absolute():
            # 相对路径相对于 ai/ 目录
            p = Path(__file__).resolve().parent / p
        return p
    # 默认：ai/docs/
    return Path(__file__).resolve().parent / "docs"


@lru_cache()
def get_ai_config() -> AIConfig:
    """
    获取 AI 配置单例
    所有值从环境变量读取（由 ai/.env 注入）
    注意：qdrant_collection_name 可能被指针文件覆盖（见 get_active_collection）
    """
    return AIConfig(
        # DeepSeek
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        llm_connect_timeout=float(os.getenv("LLM_CONNECT_TIMEOUT", "3.0")),
        llm_read_timeout=float(os.getenv("LLM_READ_TIMEOUT", "30.0")),

        vision_api_key=os.getenv("VISION_API_KEY", ""),
        vision_base_url=os.getenv("VISION_BASE_URL", ""),
        vision_model=os.getenv("VISION_MODEL", "gpt-4o"),

        minio_endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", ""),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", ""),
        minio_bucket=os.getenv("MINIO_BUCKET", "helpdesk"),
        minio_secure=os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes"),
        minio_api_prefix=os.getenv("MINIO_API_PREFIX", ""),
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
        # 诊断服务
        diagnosis_scan_interval=int(os.getenv("DIAGNOSIS_SCAN_INTERVAL", "60")),
        # 派单后台
        assign_scan_interval=int(os.getenv("ASSIGN_SCAN_INTERVAL", "120")),
        # Debug
        debug_assign_to_admin=os.getenv("DEBUG_ASSIGN_TO_ADMIN", "false").lower() in ("1", "true", "yes"),
        # Meilisearch
        meili_enabled=os.getenv("MEILI_ENABLED", "true").lower() in ("1", "true", "yes"),
        meili_host_url=os.getenv("MEILI_HOST_URL", "http://localhost:7700"),
        meili_master_key=os.getenv("MEILI_MASTER_KEY", ""),
        # 派单
        dispatch_api_url=os.getenv("DISPATCH_API_URL", ""),
        upload_dir=os.getenv("UPLOAD_DIR", "./uploads"),
        # 文档路径
        docs_path=os.getenv("DOCS_PATH", ""),
        media_url_prefix=os.getenv("MEDIA_URL_PREFIX", "/api/ai/media"),
        code_skill_paths=os.getenv("CODE_SKILL_PATHS", ""),
        # 企业微信
        wecom_corpid=os.getenv("WECOM_CORPID", ""),
        wecom_corpsecret=os.getenv("WECOM_CORPSECRET", ""),
        wecom_docid=os.getenv("WECOM_DOCID", ""),
        wecom_sheet_id=os.getenv("WECOM_SHEET_ID", ""),

    )


async def validate_ai_config() -> dict:
    """
    启动时连通性检查（失败不阻止启动）
    """
    from ai.core import get_llm_client, QdrantClientWrapper, get_memory_manager

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
        if await redis_mgr.health_check():
            results["redis"] = {"status": "ok", "message": "连接成功"}
        else:
            results["redis"] = {"status": "error", "message": "连接超时，使用内存/MySQL降级"}
    except Exception as e:
        results["redis"] = {"status": "error", "message": str(e)}

    return results
