# 路径: ai/core/reranker.py
"""
Cross-encoder Reranker — 对检索候选结果做精排。

使用 sentence-transformers 的 CrossEncoder 加载本地模型。
模型推荐：bge-reranker-v2-m3（BAAI，多语言，中文优化）
"""
import asyncio
from typing import List, Optional, TYPE_CHECKING

from ai.exceptions import EmbeddingError

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


class RerankerClient:
    """Cross-encoder 重排序客户端。惰性加载，线程池推理。"""

    def __init__(self, model_path: str, device: str = "cpu", max_length: int = 512):
        self.model_path = model_path
        self.device = device
        self.max_length = max_length
        self._model: Optional["CrossEncoder"] = None
        self._model_lock = asyncio.Lock()

    async def _ensure_model(self):
        if self._model is None:
            async with self._model_lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder
                    from pathlib import Path as _Path

                    model_path = self.model_path
                    local = _Path(model_path)
                    if not local.is_absolute():
                        local = _Path(__file__).resolve().parent.parent / model_path
                    if local.exists():
                        model_path = str(local.resolve())

                    try:
                        self._model = CrossEncoder(
                            model_path,
                            device=self.device,
                            max_length=self.max_length,
                        )
                    except Exception as e:
                        raise EmbeddingError(f"Reranker 模型加载失败: {str(e)}")
        return self._model

    async def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int,
    ) -> List[float]:
        """
        Args:
            query: 查询文本
            documents: 候选文档列表
            top_k: 保留数量

        Returns:
            每个文档的相关性分数（越高越相关）
        """
        if not documents:
            return []

        model = await self._ensure_model()
        pairs = [[query, doc] for doc in documents]

        loop = asyncio.get_event_loop()
        try:
            scores = await loop.run_in_executor(
                None,
                lambda: model.predict(pairs, show_progress_bar=False)
            )
        except Exception as e:
            raise EmbeddingError(f"Reranker 推理失败: {str(e)}")

        # scores is a numpy array or list of floats
        return list(scores) if hasattr(scores, '__iter__') else [float(scores)]


# ── 全局单例 ─────────────────────────────────────────────────────

_reranker_client: Optional[RerankerClient] = None
_client_lock = asyncio.Lock()


async def get_reranker_client() -> Optional[RerankerClient]:
    """获取 Reranker 客户端（可能为 None，如果未配置）"""
    global _reranker_client

    if _reranker_client is None:
        async with _client_lock:
            if _reranker_client is None:
                from ai.config import get_ai_config
                config = get_ai_config()
                if config.reranker_model_path:
                    _reranker_client = RerankerClient(
                        model_path=config.reranker_model_path,
                        device=config.embedding_device,
                    )
                # 未配置时保持 None
    return _reranker_client


async def close_reranker_client() -> None:
    global _reranker_client
    _reranker_client = None
