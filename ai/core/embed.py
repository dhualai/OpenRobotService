# 路径: ai/core/embed.py
"""
统一 Embedding 接口
- 支持多模型切换
- 异步向量化
- 结果缓存
"""
import asyncio
import hashlib
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

import numpy as np

from ai.config import get_ai_config
from ai.exceptions import EmbeddingError

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


# ============================================================
# Embedding 缓存
# ============================================================

_embedding_cache: dict = {}
_cache_lock = asyncio.Lock()


def _text_hash(text: str) -> str:
    """生成文本哈希"""
    return hashlib.md5(text.encode()).hexdigest()


def _is_sentence_transformer_dir(path: Path) -> bool:
    if not path.is_dir() or not (path / "config.json").exists():
        return False
    return (path / "pytorch_model.bin").exists() or (path / "model.safetensors").exists()


def _hf_cache_snapshot(repo_id: str) -> Optional[Path]:
    """本机 HuggingFace 缓存里已下好的 snapshot。HF_HUB_OFFLINE=1 时也能用。"""
    repo = (repo_id or "").strip().strip("/")
    if not repo:
        return None
    cache = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo.replace('/', '--')}"
    ref = cache / "refs" / "main"
    if ref.is_file():
        snap = cache / "snapshots" / ref.read_text(encoding="utf-8").strip()
        if _is_sentence_transformer_dir(snap):
            return snap
    snaps = cache / "snapshots"
    if snaps.is_dir():
        for child in sorted(snaps.iterdir(), reverse=True):
            if _is_sentence_transformer_dir(child):
                return child
    return None


def resolve_embed_model_path(model_name: str, local_path: str = "") -> str:
    """服务器路径优先；不存在则 LOCAL / 仓库旁数据目录 / HF 缓存。"""
    name = (model_name or "").strip()
    local = (local_path or "").strip()
    ai_root = Path(__file__).resolve().parent.parent
    data_root = ai_root.parent / "OpenRobotService_Data" / "embed_models"
    stem = Path(name or local).name

    candidates: List[Path] = []
    if name:
        p = Path(name)
        candidates.append(p)
        if not p.is_absolute():
            candidates.append(ai_root / name)
    if local:
        candidates.append(Path(local))
    if stem:
        candidates.append(ai_root / "embed_models" / stem)
        candidates.append(data_root / stem)

    seen = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if _is_sentence_transformer_dir(cand):
            return str(cand.resolve())

    repo = ""
    if name and not Path(name).is_absolute() and name.count("/") == 1 and "\\" not in name:
        repo = name
    elif stem.startswith("bge-"):
        repo = f"BAAI/{stem}"
    cached = _hf_cache_snapshot(repo) if repo else None
    if cached is not None:
        return str(cached.resolve())

    tried = " / ".join(str(c) for c in candidates if str(c).strip()) or "(空)"
    raise EmbeddingError(f"模型加载失败: 找不到 embedding 目录（已试 {tried}）")


# ============================================================
# Embedding 客户端
# ============================================================

class EmbedClient:
    """
    统一 Embedding 客户端

    特性：
    - 本地加载 sentence-transformers 模型
    - 批量向量化
    - 内存缓存
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        device: str = "cpu",
        cache_size: int = 10000,
        local_path: str = "",
    ):
        self.model_name = model_name
        self.local_path = local_path
        self.device = device
        self.cache_size = cache_size
        self._model = None  # type: ignore
        self._model_lock = asyncio.Lock()

    async def _ensure_model(self):
        """确保模型已加载（懒加载，优先使用本地模型）"""
        if self._model is None:
            async with self._model_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    model_path = resolve_embed_model_path(self.model_name, self.local_path)
                    try:
                        # 模型加载很慢（数秒），必须放到线程池，否则持锁期间阻塞事件循环：
                        # 三路并发 retrieve_domain 的 embed() 会排队等这把锁，整个循环卡死，
                        # SSE 流中断、前端永远「思考中」。
                        loop = asyncio.get_event_loop()
                        self._model = await loop.run_in_executor(
                            None,
                            lambda: SentenceTransformer(model_path, device=self.device),
                        )
                    except EmbeddingError:
                        raise
                    except Exception as e:
                        raise EmbeddingError(f"模型加载失败: {str(e)}")
        return self._model

    async def embed(
        self,
        text: str,
        normalize: bool = True,
    ) -> np.ndarray:
        """
        单条文本向量化

        Args:
            text: 输入文本
            normalize: 是否归一化

        Returns:
            numpy.ndarray: 嵌入向量
        """
        # 检查缓存
        text_hash = _text_hash(text)
        if text_hash in _embedding_cache:
            return _embedding_cache[text_hash].copy()

        # 加载模型
        model = await self._ensure_model()

        # 在线程池执行
        loop = asyncio.get_event_loop()
        try:
            embedding = await loop.run_in_executor(
                None,
                lambda: model.encode(text, normalize_embeddings=normalize, show_progress_bar=False)
            )
        except Exception as e:
            raise EmbeddingError(f"向量化失败: {str(e)}")

        # 更新缓存
        async with _cache_lock:
            _embedding_cache[text_hash] = embedding.copy()
            if len(_embedding_cache) > self.cache_size:
                # 清理一半缓存
                keys = list(_embedding_cache.keys())[:self.cache_size // 2]
                for k in keys:
                    del _embedding_cache[k]

        return embedding

    async def embed_batch(
        self,
        texts: List[str],
        normalize: bool = True,
        batch_size: int = 32,
    ) -> List[np.ndarray]:
        """
        批量文本向量化

        Args:
            texts: 输入文本列表
            normalize: 是否归一化
            batch_size: 批处理大小

        Returns:
            List[numpy.ndarray]: 嵌入向量列表
        """
        if not texts:
            return []

        # 分离命中/未命中缓存
        results: List[Optional[np.ndarray]] = [None] * len(texts)
        uncached_texts: List[str] = []
        uncached_indices: List[int] = []

        async with _cache_lock:
            for i, text in enumerate(texts):
                text_hash = _text_hash(text)
                if text_hash in _embedding_cache:
                    results[i] = _embedding_cache[text_hash].copy()
                else:
                    uncached_texts.append(text)
                    uncached_indices.append(i)

        # 全部命中缓存
        if not uncached_texts:
            return results  # type: ignore

        # 批量向量化
        model = await self._ensure_model()
        loop = asyncio.get_event_loop()

        try:
            embeddings = await loop.run_in_executor(
                None,
                lambda: model.encode(
                    uncached_texts,
                    batch_size=batch_size,
                    normalize_embeddings=normalize,
                    show_progress_bar=False,
                )
            )
        except Exception as e:
            raise EmbeddingError(f"批量向量化失败: {str(e)}")

        # 更新缓存
        async with _cache_lock:
            for i, (text, embedding) in enumerate(zip(uncached_texts, embeddings)):
                text_hash = _text_hash(text)
                _embedding_cache[text_hash] = embedding.copy()
                results[uncached_indices[i]] = embedding.copy()

        return results  # type: ignore

    async def get_dimension(self) -> int:
        """获取向量维度"""
        model = await self._ensure_model()
        return model.get_sentence_embedding_dimension()


# ============================================================
# 全局客户端单例
# ============================================================

_embed_client: Optional[EmbedClient] = None
_client_lock = asyncio.Lock()


async def get_embed_client() -> EmbedClient:
    """获取 Embedding 客户端单例"""
    global _embed_client

    if _embed_client is None:
        async with _client_lock:
            if _embed_client is None:
                config = get_ai_config()
                _embed_client = EmbedClient(
                    model_name=config.embedding_model_name,
                    local_path=config.embedding_model_local,
                    device=config.embedding_device,
                    cache_size=config.embedding_cache_size,
                )

    return _embed_client


async def close_embed_client() -> None:
    """关闭 Embedding 客户端"""
    global _embed_client

    if _embed_client is not None:
        _embed_client._model = None
        _embed_client = None


# ============================================================
# 便捷函数
# ============================================================

async def embed_text(text: str) -> np.ndarray:
    """快捷调用单条向量化"""
    client = await get_embed_client()
    return await client.embed(text)


async def embed_texts(texts: List[str]) -> List[np.ndarray]:
    """快捷调用批量向量化"""
    client = await get_embed_client()
    return await client.embed_batch(texts)
