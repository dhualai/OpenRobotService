# 路径: ai/core/retrieval.py
"""
统一检索服务
- 向量检索（语义相似度）
- 稀疏检索（BM25 关键词匹配）
- 混合检索（RRF 融合）

所有 Qdrant 同步调用均通过 run_in_executor 在独立线程中执行，绝不阻塞事件循环。
"""
import asyncio
import re
import time
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    SearchParams,
    SparseVector
)

from ai.config import get_ai_config
from ai.exceptions import (
    RetrieveEmptyError,
    LowConfidenceError,
    ServiceUnavailableError,
)


# ============================================================
# 检索结果数据结构
# ============================================================

@dataclass
class RetrievalResult:
    """检索结果"""
    id: str
    score: float
    title: str
    content: str
    vector_score: float = 0.0
    sparse_score: float = 0.0
    images: List[str] = field(default_factory=list)


# ============================================================
# Qdrant 客户端封装
# ============================================================

class QdrantClientWrapper:
    """
    Qdrant 客户端封装

    特性：
    - 所有 Qdrant 同步调用均通过 run_in_executor 在独立线程中执行，
      绝不阻塞 FastAPI 事件循环
    - 快速失败：Qdrant 不可用时 30s 内跳过所有调用
    - 连接池复用、集合自动管理
    """

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        collection_name: str = "",
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._collection_fallback = collection_name  # 指针文件缺失时的回退
        self._client: Optional[QdrantClient] = None
        self._lock = asyncio.Lock()
        self._is_local: Optional[bool] = None  # 延迟判断
        # 快速失败
        self._unavailable: bool = False
        self._unavailable_since: float = 0.0
        self._unavailable_cooldown: float = 30.0

    @property
    def collection_name(self) -> str:
        """实时读取活跃集合名（支持热更新，无需重启）"""
        from ai.config import get_active_collection
        return get_active_collection() or self._collection_fallback

    # ------------------------------------------------------------
    # 快速失败 & 线程化调用辅助
    # ------------------------------------------------------------
    @property
    def is_unavailable(self) -> bool:
        """Qdrant 是否处于快速失败冷却期"""
        if not self._unavailable:
            return False
        if time.time() - self._unavailable_since > self._unavailable_cooldown:
            self._unavailable = False
            return False
        return True

    async def _to_thread(self, func, *args, timeout: Optional[float] = None, **kwargs):
        """
        在独立线程中执行同步 QdrantClient 调用，绝不阻塞事件循环。
        """
        loop = asyncio.get_event_loop()
        call_timeout = timeout if timeout is not None else self.timeout
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                timeout=call_timeout,
            )
        except asyncio.TimeoutError:
            self._unavailable = True
            self._unavailable_since = time.time()
            raise ServiceUnavailableError("Qdrant", f"操作超时（{call_timeout}s）")

    @classmethod
    async def from_config(cls) -> "QdrantClientWrapper":
        """从配置创建实例"""
        config = get_ai_config()
        return cls(
            host=config.qdrant_host,
            port=config.qdrant_port,
            timeout=config.qdrant_timeout,
            collection_name=config.qdrant_collection_name,
        )

    async def _ensure_client(self) -> QdrantClient:
        """确保客户端已初始化"""
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    config = get_ai_config()
                    try:
                        if config.qdrant_local_path:
                            from pathlib import Path as _Path
                            _local = _Path(config.qdrant_local_path)
                            if not _local.is_absolute():
                                # 解析为项目根目录的相对路径（不受 CWD 影响）
                                _project = _Path(__file__).resolve().parent.parent.parent
                                _local = _project / _local
                            self._client = QdrantClient(path=str(_local))
                            self._is_local = True
                        else:
                            self._client = QdrantClient(
                                host=self.host,
                                port=self.port,
                                timeout=self.timeout,
                                check_compatibility=False,
                            )
                            self._is_local = False
                    except Exception as e:
                        raise ServiceUnavailableError(
                            "Qdrant", f"连接失败: {str(e)}"
                        )
        return self._client

    # ------------------------------------------------------------
    # 集合管理（全部线程化）
    # ------------------------------------------------------------
    async def health_check(self) -> bool:
        """健康检查"""
        client = await self._ensure_client()
        try:
            return await self._to_thread(client.get_collections) is not None
        except ServiceUnavailableError:
            raise
        except Exception as e:
            raise ServiceUnavailableError("Qdrant", f"健康检查失败: {str(e)}")

    async def collection_exists(self) -> bool:
        """检查集合是否存在"""
        client = await self._ensure_client()
        return await self._to_thread(client.collection_exists, self.collection_name)

    async def create_collection(
        self,
        vector_size: int,
        sparse_vector_size: int = 10000,
    ) -> bool:
        """创建集合"""
        client = await self._ensure_client()
        if await self.collection_exists():
            return False
        try:
            await self._to_thread(
                client.create_collection,
                collection_name=self.collection_name,
                vectors_config={
                    "dense": VectorParams(
                        size=vector_size,
                        distance=Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(on_disk=False)
                    )
                },
            )
            return True
        except ServiceUnavailableError:
            raise
        except Exception as e:
            raise ServiceUnavailableError("Qdrant", f"创建集合失败: {str(e)}")

    async def create_collection_if_not_exists(
        self,
        vector_size: int,
        sparse_vector_size: int = 10000,
    ) -> bool:
        """创建集合（如果不存在）"""
        if await self.collection_exists():
            return False
        return await self.create_collection(vector_size, sparse_vector_size)

    async def list_collections(self) -> List[str]:
        """列出所有集合"""
        client = await self._ensure_client()
        try:
            collections = await self._to_thread(client.get_collections)
            return [c.name for c in collections.collections]
        except ServiceUnavailableError:
            raise
        except Exception as e:
            raise ServiceUnavailableError("Qdrant", f"列出集合失败: {str(e)}")

    # ------------------------------------------------------------
    # 检索（全部线程化 + 快速失败）
    # ------------------------------------------------------------
    async def search_dense(
        self,
        vector: List[float],
        top_k: int = 3,
        score_threshold: Optional[float] = None,
        collection_name: Optional[str] = None,
    ) -> List[Any]:
        """向量检索，可指定 collection（默认使用活跃集合）"""
        if self.is_unavailable:
            return []
        client = await self._ensure_client()
        col = collection_name or self.collection_name
        try:
            # qdrant_client >=1.18: search() → query_points()
            result = await self._to_thread(
                client.query_points,
                collection_name=col,
                query=vector,
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            )
            return result.points
        except ServiceUnavailableError:
            return []
        except Exception as e:
            raise ServiceUnavailableError("Qdrant", f"向量检索失败: {str(e)}")

    async def search_sparse(
        self,
        sparse_vector: Dict[int, float],
        top_k: int = 3,
        score_threshold: Optional[float] = None,
    ) -> List[Any]:
        """稀疏向量检索"""
        if self.is_unavailable:
            return []
        # 本地模式不支持稀疏检索，跳过
        if self._is_local:
            return []
        client = await self._ensure_client()
        try:
            # qdrant_client >=1.10: sparse vector 需要 SparseVector 对象
            indices = sorted(sparse_vector.keys())
            values = [sparse_vector[i] for i in indices]
            sv = SparseVector(indices=indices, values=values)

            # qdrant_client >=1.18: search() → query_points()
            result = await self._to_thread(
                client.query_points,
                collection_name=self.collection_name,
                query=("sparse", sv),
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
                search_params=SearchParams(
                    hnsw_ef=128,
                    exact=False,
                ),
            )
            return result.points
        except ServiceUnavailableError:
            return []
        except Exception:
            # 稀疏检索为辅助功能，API 不兼容时静默跳过
            return []

    async def upsert(
        self,
        points: List[Dict[str, Any]],
    ) -> None:
        """批量写入"""
        client = await self._ensure_client()
        try:
            await self._to_thread(
                client.upsert,
                collection_name=self.collection_name,
                points=points,
            )
        except ServiceUnavailableError:
            raise
        except Exception as e:
            raise ServiceUnavailableError("Qdrant", f"写入失败: {str(e)}")

    async def delete_collection(self) -> None:
        """删除集合"""
        client = await self._ensure_client()
        try:
            await self._to_thread(client.delete_collection, self.collection_name)
        except ServiceUnavailableError:
            pass
        except Exception:
            pass  # 集合不存在时忽略


# ============================================================
# 统一检索服务
# ============================================================

class RetrievalService:
    """
    统一检索服务

    特性：
    - 向量检索 + BM25 检索
    - RRF 融合
    - 置信度检测
    """

    def __init__(
        self,
        top_k: int = 3,
        score_threshold: float = 0.65,
    ):
        self.top_k = top_k
        self.score_threshold = score_threshold
        self._qdrant: Optional[QdrantClientWrapper] = None
        self._embed_client = None

    async def _ensure_clients(self):
        """确保客户端已初始化"""
        if self._qdrant is None:
            config = get_ai_config()
            self._qdrant = QdrantClientWrapper(
                host=config.qdrant_host,
                port=config.qdrant_port,
                timeout=config.qdrant_timeout,
                collection_name=config.qdrant_collection_name,
            )

        if self._embed_client is None:
            from ai.core.embed import get_embed_client
            self._embed_client = await get_embed_client()

    @property
    def is_qdrant_unavailable(self) -> bool:
        """Qdrant 是否不可用（供 pipeline 快速跳过）"""
        if self._qdrant is None:
            return False
        return self._qdrant.is_unavailable

    def _generate_bm25_sparse(
        self,
        query: str,
        max_index: int = 10000,
    ) -> Dict[int, float]:
        """生成 BM25 稀疏向量（字符级 n-gram）"""
        text = re.sub(r'[^\w\s一-鿿]', ' ', query)
        text = re.sub(r'\s+', ' ', text).strip()

        if not text:
            return {}

        tokens = []
        for i in range(len(text)):
            tokens.append(text[i])
            if i < len(text) - 1:
                tokens.append(text[i:i+2])

        tf: Dict[str, int] = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1

        if not tf:
            return {}

        max_tf = max(tf.values())
        sparse = {}
        for token, freq in tf.items():
            idx = hash(token) % max_index
            sparse[abs(idx)] = freq / max_tf

        return sparse

    def _rrf_fusion(
        self,
        dense_results: List[Any],
        sparse_results: List[Any],
        top_k: int,
        rrf_k: int = 60,
    ) -> List[RetrievalResult]:
        """RRF（倒数排名融合）"""
        doc_scores: Dict[str, Dict[str, Any]] = {}

        for rank, point in enumerate(dense_results):
            doc_id = str(point.id)
            if doc_id not in doc_scores:
                doc_scores[doc_id] = {
                    "dense": 0.0, "sparse": 0.0,
                    "title": "", "content": "", "images": [],
                    "vector_score": 0.0, "sparse_score": 0.0,
                }
            doc_scores[doc_id]["dense"] = 1.0 / (rrf_k + rank + 1)
            doc_scores[doc_id]["vector_score"] = point.score
            if point.payload:
                doc_scores[doc_id]["title"] = point.payload.get("title", "")
                doc_scores[doc_id]["content"] = point.payload.get("content", "")
                doc_scores[doc_id]["images"] = point.payload.get("images", [])

        for rank, point in enumerate(sparse_results):
            doc_id = str(point.id)
            if doc_id not in doc_scores:
                doc_scores[doc_id] = {
                    "dense": 0.0, "sparse": 0.0,
                    "title": "", "content": "", "images": [],
                    "vector_score": 0.0, "sparse_score": 0.0,
                }
            doc_scores[doc_id]["sparse"] = 1.0 / (rrf_k + rank + 1)
            doc_scores[doc_id]["sparse_score"] = point.score
            if point.payload and not doc_scores[doc_id]["title"]:
                doc_scores[doc_id]["title"] = point.payload.get("title", "")
                doc_scores[doc_id]["content"] = point.payload.get("content", "")
                doc_scores[doc_id]["images"] = point.payload.get("images", [])

        rrf_scores = [
            (doc_id, scores["dense"] + scores["sparse"], scores)
            for doc_id, scores in doc_scores.items()
        ]
        rrf_scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for doc_id, rrf_score, scores in rrf_scores[:top_k]:
            results.append(RetrievalResult(
                id=doc_id,
                score=rrf_score,
                title=scores["title"],
                content=scores["content"],
                vector_score=scores["vector_score"],
                sparse_score=scores["sparse_score"],
                images=scores.get("images", []),

            ))

        return results

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        check_confidence: bool = True,
    ) -> Tuple[List[RetrievalResult], float]:
        """
        混合检索

        Args:
            query: 查询文本
            top_k: 返回数量
            check_confidence: 是否检查置信度

        Returns:
            Tuple[List[RetrievalResult], float]: 检索结果和 Top-1 得分

        Raises:
            RetrieveEmptyError: 无检索结果
        """
        k = top_k or self.top_k
        await self._ensure_clients()

        # Qdrant 快速失败：不可用时直接返回空，不阻塞
        if self._qdrant.is_unavailable:
            raise RetrieveEmptyError("Qdrant 服务不可用，已跳过检索")

        # 1. 生成向量
        query_vector = await self._embed_client.embed(query)
        bm25_sparse = self._generate_bm25_sparse(query)

        # 2. 并行检索
        dense_task = self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=k * 2,
        )
        sparse_task = self._qdrant.search_sparse(
            bm25_sparse,
            top_k=k * 2,
        ) if bm25_sparse else asyncio.sleep(0)

        dense_results, sparse_results = await asyncio.gather(dense_task, sparse_task)
        # 3. RRF 融合
        results = self._rrf_fusion(
            dense_results,
            sparse_results if bm25_sparse else [],
            top_k=k,
        )

        if not results:
            raise RetrieveEmptyError("未找到相关文档")

        # 使用原始向量相似度做置信度检查（RRF score 是排序分数，不适合做阈值比较）
        top1_score = results[0].vector_score if results else 0.0

        # 4. 置信度检查
        if check_confidence and top1_score < self.score_threshold:
            raise LowConfidenceError(
                f"检索置信度较低 ({top1_score:.2f} < {self.score_threshold})",
                confidence=top1_score,
            )

        return results, top1_score

    async def retrieve_faq(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[RetrievalResult]:
        """
        FAQ 知识库检索（仅向量检索，不做置信度检查）。

        FAQ 条目通常较短，向量相似度足够区分，不需要 BM25。
        使用独立的 FAQ 指针文件热切换集合。
        """
        from ai.config import get_active_faq_collection

        faq_col = get_active_faq_collection()
        if not faq_col:
            return []  # FAQ 集合尚未入库

        k = top_k or self.top_k
        await self._ensure_clients()

        if self._qdrant.is_unavailable:
            return []

        query_vector = await self._embed_client.embed(query)
        points = await self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=k,
            collection_name=faq_col,
        )

        results = []
        for point in points:
            payload = point.payload or {}
            results.append(RetrievalResult(
                id=str(point.id),
                score=point.score,
                title=payload.get("question", ""),
                content=payload.get("answer", ""),
                vector_score=point.score,
            ))
        return results

    async def retrieve_troubleshooting(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        问题排查树检索（仅向量检索，不做置信度检查）。

        排查树条目是结构化的步骤文本，向量相似度足够，不需要 BM25。
        使用独立的排查树指针文件热切换集合。
        top_k 默认为 3（比 FAQ 多 1），因为模糊查询需多返回候选项做分流。
        """
        from ai.config import get_active_troubleshooting_collection

        ts_col = get_active_troubleshooting_collection()
        if not ts_col:
            return []  # 排查树集合尚未入库

        k = top_k or 3
        await self._ensure_clients()

        if self._qdrant.is_unavailable:
            return []

        query_vector = await self._embed_client.embed(query)
        points = await self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=k,
            collection_name=ts_col,
        )

        results = []
        for point in points:
            payload = point.payload or {}
            results.append(RetrievalResult(
                id=str(point.id),
                score=point.score,
                title=payload.get("symptom_name", ""),
                content=payload.get("linearized_tree", ""),
                vector_score=point.score,
            ))
        return results

    async def retrieve_cheduan(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        车端错误码检索（仅向量检索）。

        读取车端错误码集合，匹配错误码或错误描述。
        """
        from ai.config import get_active_cheduan_collection

        cd_col = get_active_cheduan_collection()
        if not cd_col:
            return []

        k = top_k or 3
        await self._ensure_clients()

        if self._qdrant.is_unavailable:
            return []

        query_vector = await self._embed_client.embed(query)
        points = await self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=k,
            collection_name=cd_col,
        )

        results = []
        for point in points:
            payload = point.payload or {}
            results.append(RetrievalResult(
                id=str(point.id),
                score=point.score,
                title=f"车端错误码 {payload.get('error_code', '')}",
                content=(
                    f"错误码：{payload.get('error_code', '')}\n"
                    f"类别：{payload.get('category', '')}\n"
                    f"等级：{payload.get('level', '')}\n"
                    f"描述：{payload.get('description_cn', '')}\n"
                    f"方案：{payload.get('solution_cn', '')}"
                ),
                vector_score=point.score,
            ))
        return results

    async def retrieve_translation(
        self,
        query: str,
        top_k: int = 2,
    ) -> List[RetrievalResult]:
        """
        USP 翻译表检索（仅向量检索）。

        读取翻译表集合，匹配 UI 标签、错误码说明等。
        """
        from ai.config import get_active_translation_collection

        tr_col = get_active_translation_collection()
        if not tr_col:
            return []

        k = top_k or 2
        await self._ensure_clients()

        if self._qdrant.is_unavailable:
            return []

        query_vector = await self._embed_client.embed(query)
        points = await self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=k,
            collection_name=tr_col,
        )

        results = []
        for point in points:
            payload = point.payload or {}
            samples = payload.get("sample_entries", [])
            sample_text = "\n".join(
                f"  {s['cn']} | {s['en']}"
                for s in samples[:10]
            )
            results.append(RetrievalResult(
                id=str(point.id),
                score=point.score,
                title=f"翻译表 [{payload.get('namespace', '')}]",
                content=(
                    f"namespace: {payload.get('namespace', '')}\n"
                    f"共 {payload.get('entry_count', 0)} 条\n"
                    f"示例：\n{sample_text}"
                ),
                vector_score=point.score,
            ))
        return results

    async def ensure_collection(self, vector_size: int) -> None:
        """确保集合存在"""
        await self._ensure_clients()
        await self._qdrant.create_collection_if_not_exists(vector_size)


# ============================================================
# 全局检索服务单例
# ============================================================

_retrieval_service: Optional[RetrievalService] = None
_service_lock = asyncio.Lock()


async def get_retrieval_service() -> RetrievalService:
    """获取检索服务单例"""
    global _retrieval_service

    if _retrieval_service is None:
        async with _service_lock:
            if _retrieval_service is None:
                config = get_ai_config()
                _retrieval_service = RetrievalService(
                    top_k=config.retrieval_top_k,
                    score_threshold=config.retrieval_score_threshold,
                )

    return _retrieval_service


# ============================================================
# 便捷函数
# ============================================================

async def retrieve(
    query: str,
    top_k: Optional[int] = None,
) -> List[RetrievalResult]:
    """快捷检索"""
    service = await get_retrieval_service()
    results, _ = await service.retrieve(query, top_k, check_confidence=False)
    return results
