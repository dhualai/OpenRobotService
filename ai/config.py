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

# 指针文件：记录当前活跃的 Qdrant 集合名（位于 ai/kb/ 下）
_KB_DIR = Path(__file__).resolve().parent / "kb"
_ACTIVE_COLLECTION_POINTER = _KB_DIR / "active_collection.txt"
_ACTIVE_FAQ_COLLECTION_POINTER = _KB_DIR / "active_faq_collection.txt"
_ACTIVE_TROUBLESHOOTING_COLLECTION_POINTER = _KB_DIR / "active_troubleshooting_collection.txt"
_ACTIVE_PLATFORM_FAQ_COLLECTION_POINTER = _KB_DIR / "active_platform_faq_collection.txt"
_ACTIVE_CHEDUAN_COLLECTION_POINTER = _KB_DIR / "active_cheduan_collection.txt"
_ACTIVE_TRANSLATION_COLLECTION_POINTER = _KB_DIR / "active_translation_collection.txt"
_ACTIVE_CHEDUAN_MANUAL_COLLECTION_POINTER = _KB_DIR / "active_cheduan_manual_collection.txt"
_ACTIVE_TASK_RESOLUTIONS_POINTER = _KB_DIR / "active_task_resolutions_collection.txt"
_ACTIVE_USP_DIAGNOSIS_POINTER = _KB_DIR / "active_usp_diagnosis_collection.txt"


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
    assign_scan_interval: int = Field(default=60, description="派单 Worker 扫描待派单工单间隔（秒）")
    upload_dir: str = Field(default="./uploads", description="附件上传目录")

    # ========== 诊断服务 ==========
    diagnosis_scan_interval: int = Field(default=60, description="诊断服务扫描新工单间隔（秒）")

    # ========== MinIO 对象存储（附件图片读取）==========
    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_bucket: str = Field(default="helpdesk")
    minio_secure: bool = Field(default=False)

    # ========== 企业微信 ==========
    wecom_corpid: str = Field(default="", description="企业微信 企业ID")
    wecom_corpsecret: str = Field(default="", description="企业微信 应用Secret")
    wecom_docid: str = Field(default="", description="企业微信 Smartsheet 文档ID")
    wecom_sheet_id: str = Field(default="", description="企业微信 Smartsheet 子表ID")

    # ========== Debug ==========
    debug_assign_to_admin: bool = Field(default=False, description="开发模式：所有工单直接分配给 admin，跳过 AI 派单")

    # ========== 超时 ==========
    ai_chain_timeout: float = Field(default=2.5)

    # ========== 文档路径 ==========
    docs_path: str = Field(default="", description="原始文档根目录，默认 ai/docs/")

    # ========== CodeSkill 代码检索 ==========
    code_skill_paths: str = Field(default="", description="代码索引根目录，逗号分隔")
    media_url_prefix: str = Field(default="/api/ai/media", description="媒体文件 URL 前缀，用于前端渲染图片")


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


def get_active_platform_faq_collection() -> str:
    """读取当前活跃的 platform FAQ Qdrant 集合名"""
    try:
        if _ACTIVE_PLATFORM_FAQ_COLLECTION_POINTER.exists():
            name = _ACTIVE_PLATFORM_FAQ_COLLECTION_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_platform_faq_collection(name: str) -> None:
    """写入活跃 platform FAQ 集合指针（入库脚本调用）"""
    _ACTIVE_PLATFORM_FAQ_COLLECTION_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_PLATFORM_FAQ_COLLECTION_POINTER.write_text(name, encoding="utf-8")


def get_active_troubleshooting_collection() -> str:
    """读取当前活跃的 troubleshooting Qdrant 集合名"""
    try:
        if _ACTIVE_TROUBLESHOOTING_COLLECTION_POINTER.exists():
            name = _ACTIVE_TROUBLESHOOTING_COLLECTION_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_troubleshooting_collection(name: str) -> None:
    """写入活跃 troubleshooting 集合指针（入库脚本调用）"""
    _ACTIVE_TROUBLESHOOTING_COLLECTION_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_TROUBLESHOOTING_COLLECTION_POINTER.write_text(name, encoding="utf-8")


def get_active_cheduan_collection() -> str:
    """读取当前活跃的车端错误码 Qdrant 集合名"""
    try:
        if _ACTIVE_CHEDUAN_COLLECTION_POINTER.exists():
            name = _ACTIVE_CHEDUAN_COLLECTION_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_cheduan_collection(name: str) -> None:
    """写入活跃车端错误码集合指针"""
    _ACTIVE_CHEDUAN_COLLECTION_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_CHEDUAN_COLLECTION_POINTER.write_text(name, encoding="utf-8")


def get_active_translation_collection() -> str:
    """读取当前活跃的翻译表 Qdrant 集合名"""
    try:
        if _ACTIVE_TRANSLATION_COLLECTION_POINTER.exists():
            name = _ACTIVE_TRANSLATION_COLLECTION_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_translation_collection(name: str) -> None:
    """写入活跃翻译表集合指针"""
    _ACTIVE_TRANSLATION_COLLECTION_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_TRANSLATION_COLLECTION_POINTER.write_text(name, encoding="utf-8")


def get_active_cheduan_manual_collection() -> str:
    """读取当前活跃的车端实施手册 Qdrant 集合名"""
    try:
        if _ACTIVE_CHEDUAN_MANUAL_COLLECTION_POINTER.exists():
            name = _ACTIVE_CHEDUAN_MANUAL_COLLECTION_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_cheduan_manual_collection(name: str) -> None:
    """写入活跃车端实施手册集合指针"""
    _ACTIVE_CHEDUAN_MANUAL_COLLECTION_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_CHEDUAN_MANUAL_COLLECTION_POINTER.write_text(name, encoding="utf-8")


def get_active_task_resolutions_collection() -> str:
    """读取当前活跃的任务解决方案 Qdrant 集合名"""
    try:
        if _ACTIVE_TASK_RESOLUTIONS_POINTER.exists():
            name = _ACTIVE_TASK_RESOLUTIONS_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_task_resolutions_collection(name: str) -> None:
    """写入活跃任务解决方案集合指针"""
    _ACTIVE_TASK_RESOLUTIONS_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_TASK_RESOLUTIONS_POINTER.write_text(name, encoding="utf-8")


def get_active_usp_diagnosis_collection() -> str:
    """读取当前活跃的 USP 诊断知识库 Qdrant 集合名"""
    try:
        if _ACTIVE_USP_DIAGNOSIS_POINTER.exists():
            name = _ACTIVE_USP_DIAGNOSIS_POINTER.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def _write_active_usp_diagnosis_collection(name: str) -> None:
    """写入活跃 USP 诊断知识库集合指针"""
    _ACTIVE_USP_DIAGNOSIS_POINTER.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_USP_DIAGNOSIS_POINTER.write_text(name, encoding="utf-8")


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
        assign_scan_interval=int(os.getenv("ASSIGN_SCAN_INTERVAL", "60")),
        # Debug
        debug_assign_to_admin=os.getenv("DEBUG_ASSIGN_TO_ADMIN", "false").lower() in ("1", "true", "yes"),
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
