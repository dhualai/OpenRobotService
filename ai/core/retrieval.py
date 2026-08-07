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

from ai.core.logging import get_logger

logger = get_logger(__name__)
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
    sub_domain: str = ""
    domain: str = ""


# ============================================================
# BM25 稀疏向量生成（入库和检索共用）
# ============================================================

def generate_bm25_sparse(text: str, max_index: int = 10000) -> Dict[int, float]:
    """字符级 n-gram → BM25 稀疏向量。

    tokenization: unigram + bigram 字符，hash 取模映射到固定维度。
    入库和检索使用完全相同的函数，保证 hash 一致。
    """
    text = re.sub(r'[^\w\s一-鿿]', ' ', text)
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

    import hashlib

    max_tf = max(tf.values())
    sparse = {}
    for token, freq in tf.items():
        # 用 md5 替代 Python 内置 hash()——后者跨进程不一致（PYTHONHASHSEED），
        # 导致入库和查询的稀疏索引对不上，BM25 永远为空。
        idx = int.from_bytes(hashlib.md5(token.encode()).digest()[:4], 'big') % max_index
        sparse[idx] = freq / max_tf

    return sparse


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
        query_filter: Optional[Any] = None,
    ) -> List[Any]:
        """向量检索，可指定 collection（默认使用活跃集合）。支持 payload filter 精确过滤。"""
        if self.is_unavailable:
            return []
        client = await self._ensure_client()
        col = collection_name or self.collection_name
        # 优先使用命名向量 "dense"（新集合格式），失败时回退到未命名向量（旧集合格式）
        for using in ("dense", None):
            try:
                kwargs = dict(
                    collection_name=col,
                    query=vector,
                    limit=top_k,
                    score_threshold=score_threshold,
                    with_payload=True,
                )
                if using is not None:
                    kwargs["using"] = using
                if query_filter is not None:
                    kwargs["query_filter"] = query_filter
                result = await self._to_thread(client.query_points, **kwargs)
                return result.points
            except ServiceUnavailableError:
                return []
            except Exception as e:
                if using == "dense":
                    # 可能是旧集合没有命名向量，回退到未命名向量再试一次
                    logger.debug(f"[Qdrant] dense 命名向量检索失败 ({e})，回退未命名向量")
                    continue
                raise ServiceUnavailableError("Qdrant", f"向量检索失败: {str(e)}")

    async def search_sparse(
        self,
        sparse_vector: Dict[int, float],
        top_k: int = 3,
        score_threshold: Optional[float] = None,
        collection_name: Optional[str] = None,
    ) -> List[Any]:
        """稀疏向量检索，可指定 collection（默认使用活跃集合）。"""
        if self.is_unavailable:
            return []
        # 本地模式不支持稀疏检索，跳过
        if self._is_local:
            return []
        client = await self._ensure_client()
        col = collection_name or self.collection_name
        try:
            # qdrant_client >=1.10: sparse vector 需要 SparseVector 对象
            indices = sorted(sparse_vector.keys())
            values = [sparse_vector[i] for i in indices]
            sv = SparseVector(indices=indices, values=values)

            # qdrant_client >=1.18: search() → query_points()
            result = await self._to_thread(
                client.query_points,
                collection_name=col,
                query=sv,
                using="sparse",
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

    async def upsert_to_collection(
        self,
        collection_name: str,
        vectors: List,
        ids: List[str],
        payloads: List[dict],
    ) -> bool:
        """写入到指定 collection（支持跨 collection 操作）"""
        from qdrant_client.models import PointStruct
        client = await self._ensure_client()
        points = [
            PointStruct(id=idx, vector=vec, payload=pl)
            for idx, vec, pl in zip(ids, vectors, payloads)
        ]
        try:
            await self._to_thread(
                client.upsert,
                collection_name=collection_name,
                points=points,
            )
            return True
        except Exception as e:
            logger.error(f"Qdrant 写入失败: collection={collection_name}, error={e}", exc_info=True)
            print(f"  [qdrant] upsert_to_collection({collection_name}) failed: {e}")
            return False

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
        self._reranker = None  # cross-encoder reranker, loaded lazily

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

        if self._reranker is None:
            from ai.core.reranker import get_reranker_client
            self._reranker = await get_reranker_client()

    async def _rerank_results(
        self,
        query: str,
        results: List[RetrievalResult],
        top_k: int,
    ) -> List[RetrievalResult]:
        """用 cross-encoder 对候选结果精排，返回 top_k。

        CPU 推理瓶颈：bge-reranker-v2-m3 在 CPU 上约 700ms/pair，
        候选数封顶在 15，避免单次检索耗时超过 10 秒。
        """
        _MAX_RERANK_CANDIDATES = 8
        if not self._reranker or len(results) <= top_k:
            return results[:top_k]

        capped = results[:_MAX_RERANK_CANDIDATES]
        docs = [r.content[:400] for r in capped]
        try:
            import time as _time
            _t0 = _time.perf_counter()
            scores = await self._reranker.rerank(query, docs, top_k)
            _elapsed = _time.perf_counter() - _t0
            if _elapsed > 1.0:
                logger.info(f"[reranker] {len(capped)} 对耗时 {_elapsed:.1f}s")
        except Exception:
            logger.warning(f"[reranker] 推理失败，降级为原始排序", exc_info=True)
            return results[:top_k]

        scored = list(zip(capped, scores))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [r for r, _ in scored[:top_k]]

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
        """生成 BM25 稀疏向量（委托给模块级函数）"""
        return generate_bm25_sparse(query, max_index)

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
                    "sub_domain": "", "domain": "",
                }
            doc_scores[doc_id]["dense"] = 1.0 / (rrf_k + rank + 1)
            doc_scores[doc_id]["vector_score"] = point.score
            if point.payload:
                doc_scores[doc_id]["title"] = point.payload.get("title", "")
                doc_scores[doc_id]["content"] = point.payload.get("content", "")
                doc_scores[doc_id]["images"] = point.payload.get("images", [])
                doc_scores[doc_id]["sub_domain"] = point.payload.get("sub_domain", "")
                doc_scores[doc_id]["domain"] = point.payload.get("domain", "")

        for rank, point in enumerate(sparse_results):
            doc_id = str(point.id)
            if doc_id not in doc_scores:
                doc_scores[doc_id] = {
                    "dense": 0.0, "sparse": 0.0,
                    "title": "", "content": "", "images": [],
                    "vector_score": 0.0, "sparse_score": 0.0,
                    "sub_domain": "", "domain": "",
                }
            doc_scores[doc_id]["sparse"] = 1.0 / (rrf_k + rank + 1)
            doc_scores[doc_id]["sparse_score"] = point.score
            if point.payload and not doc_scores[doc_id]["title"]:
                doc_scores[doc_id]["title"] = point.payload.get("title", "")
                doc_scores[doc_id]["content"] = point.payload.get("content", "")
                doc_scores[doc_id]["images"] = point.payload.get("images", [])
                doc_scores[doc_id]["sub_domain"] = point.payload.get("sub_domain", "")
                doc_scores[doc_id]["domain"] = point.payload.get("domain", "")

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
                sub_domain=scores.get("sub_domain", ""),
                domain=scores.get("domain", ""),
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

        # 2. 并行检索（reranker 候选数收窄，CPU 推理开销大）
        _rerank_margin = min(k + 4, 15)
        candidate_k = _rerank_margin if self._reranker else k * 2
        dense_task = self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=candidate_k,
        )
        sparse_task = self._qdrant.search_sparse(
            bm25_sparse,
            top_k=candidate_k,
        ) if bm25_sparse else asyncio.sleep(0)

        dense_results, sparse_results = await asyncio.gather(dense_task, sparse_task)
        # 3. RRF 融合
        fuse_k = candidate_k if self._reranker else k
        results = self._rrf_fusion(
            dense_results,
            sparse_results if bm25_sparse else [],
            top_k=fuse_k,
        )

        # 4. cross-encoder 重排序
        if self._reranker and len(results) > k:
            results = await self._rerank_results(query, results, k)

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

    async def retrieve_domain(
        self,
        query: str,
        domain: str,
        top_k: int = 3,
        sub_domain: Optional[str] = None,
        query_filter=None,
    ) -> List[RetrievalResult]:
        """
        通用 domain 检索：给定 domain 和可选 sub_domain，从对应 Qdrant 集合中检索。

        Args:
            query: 查询文本
            domain: 五层 domain（industry/company/team/project/personal）
            top_k: 返回数量
            sub_domain: 可选的子域过滤（如 "faq", "cheduan_errors"），
                        设置后通过 Qdrant payload filter 只返回该子域的 chunks
            query_filter: 额外的 Qdrant Filter（用于高级场景如错误码精确匹配）

        Returns:
            List[RetrievalResult]
        """
        from ai.config import get_active_collection_for

        col = get_active_collection_for(domain)
        if not col:
            return []

        await self._ensure_clients()
        if self._qdrant.is_unavailable:
            return []

        # 构建 payload filter
        if sub_domain:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            sd_filter = Filter(
                must=[FieldCondition(key="sub_domain", match=MatchValue(value=sub_domain))]
            )
            if query_filter is not None:
                # 合并 sub_domain filter 和外部 filter（AND 关系）
                from qdrant_client.models import Filter
                if hasattr(query_filter, 'must') and query_filter.must:
                    combined_must = list(query_filter.must) + list(sd_filter.must)
                    query_filter = Filter(must=combined_must)
                else:
                    query_filter = sd_filter
            else:
                query_filter = sd_filter

        # hybrid search: dense + sparse 并行 → RRF 融合
        # reranker 候选数收窄：top_k + margin，硬封顶 15（CPU 推理太慢，每 pair ~700ms）
        _rerank_margin = min(top_k + 4, 8)
        candidate_k = _rerank_margin if self._reranker else top_k * 2
        query_vector = await self._embed_client.embed(query)
        bm25_sparse = self._generate_bm25_sparse(query)

        dense_task = self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=candidate_k,
            collection_name=col,
            query_filter=query_filter,
        )
        sparse_task = self._qdrant.search_sparse(
            bm25_sparse,
            top_k=candidate_k,
            collection_name=col,
        ) if bm25_sparse else asyncio.sleep(0)

        dense_res, sparse_list = await asyncio.gather(dense_task, sparse_task)

        # 清理异常（local Qdrant 上 sparse 返回空列表，抛异常时降级）
        if isinstance(sparse_list, BaseException):
            sparse_list = []
        if not isinstance(sparse_list, list):
            sparse_list = []

        # RRF 融合（保留足够候选给 reranker）
        fuse_k = candidate_k if self._reranker else top_k
        results = self._rrf_fusion(dense_res, sparse_list, top_k=fuse_k)

        # cross-encoder 重排序
        if self._reranker and len(results) > top_k:
            results = await self._rerank_results(query, results, top_k)

        return results

    async def retrieve_faq(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[RetrievalResult]:
        """
        FAQ 知识库检索（team domain，匹配 sub_domain="faq" 或 "usp_faq"）。
        """
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        faq_filter = Filter(
            must=[FieldCondition(
                key="sub_domain",
                match=MatchAny(any=["faq", "usp_faq"]),
            )]
        )
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or self.top_k,
            query_filter=faq_filter,
        )

    async def retrieve_company(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[RetrievalResult]:
        """
        company 域全量检索（产品目录、车端错误码、VDA5050协议等）。
        """
        return await self.retrieve_domain(
            query, "company",
            top_k=top_k or self.top_k,
        )

    async def retrieve_industry(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[RetrievalResult]:
        """
        industry 域全量检索（行业标准、导航规范等）。
        """
        return await self.retrieve_domain(
            query, "industry",
            top_k=top_k or self.top_k,
        )

    async def retrieve_troubleshooting(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        问题排查树/诊断参考检索（委托到 team domain，sub_domain="diagnosis"）。
        """
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or 3,
            sub_domain="diagnosis",
        )

    @staticmethod
    def _extract_error_codes(query: str) -> List[str]:
        r"""从查询中提取可能的车端错误码（3~5 位数字）。
        不能用 \b —— Python 3 re.UNICODE 默认开启，中文被视作 \w。"""
        return re.findall(r'(?<!\d)(\d{3,5})(?!\d)', query)

    async def retrieve_cheduan(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        车端错误码检索（委托到 company domain，sub_domain="cheduan_errors"）。

        策略：先从 query 中提取数字错误码做 payload filter 精确匹配，
        再叠加纯向量检索补充语义匹配结果（去重合并）。
        """
        from ai.config import get_active_collection_for

        cd_col = get_active_collection_for("company")
        if not cd_col:
            return []

        k = top_k or 3
        await self._ensure_clients()

        if self._qdrant.is_unavailable:
            return []

        codes = self._extract_error_codes(query)
        query_vector = await self._embed_client.embed(query)
        seen_ids: set = set()

        # ── 第一路：错误码精确匹配 ──
        exact_points: list = []
        if codes:
            from qdrant_client.models import Filter, FieldCondition, MatchAny
            code_filter = Filter(
                must=[FieldCondition(key="error_code", match=MatchAny(any=codes))]
            )
            try:
                exact_points = await self._qdrant.search_dense(
                    query_vector.tolist(),
                    top_k=len(codes) * 2,
                    collection_name=cd_col,
                    query_filter=code_filter,
                )
            except Exception as e:
                logger.warning(f"[cheduan] 错误码精确匹配失败: {e}")
                exact_points = []

        # ── 第二路：sub_domain 过滤 + 向量检索（精确匹配不够 k 条时补充）──
        vector_points: list = []
        if len(exact_points) < k:
            from qdrant_client.models import Filter as QFilter, FieldCondition, MatchValue
            sd_filter = QFilter(
                must=[FieldCondition(key="sub_domain", match=MatchValue(value="cheduan_errors"))]
            )
            try:
                vector_points = await self._qdrant.search_dense(
                    query_vector.tolist(),
                    top_k=k,
                    collection_name=cd_col,
                    query_filter=sd_filter,
                )
            except Exception as e:
                logger.warning(f"[cheduan] sub_domain 语义检索失败: {e}")
                vector_points = []

        # ── 合并：精确匹配优先，向量结果去重补充 ──
        def _make_result(point) -> RetrievalResult:
            payload = point.payload or {}
            # 新格式：payload 有 title/content 和可能的 error_code
            ec = payload.get("error_code", "")
            title = payload.get("title", "")
            content = payload.get("content", "")
            # 如果 payload 有 error_code，使用结构化格式；否则直接用 title/content
            if ec:
                return RetrievalResult(
                    id=str(point.id),
                    score=point.score,
                    title=f"车端错误码 {ec}",
                    content=(
                        f"错误码：{ec}\n"
                        f"类别：{payload.get('category', '')}\n"
                        f"等级：{payload.get('level', '')}\n"
                        f"描述：{payload.get('description_cn', '')}\n"
                        f"方案：{payload.get('solution_cn', '')}"
                    ),
                    vector_score=point.score,
                )
            else:
                return RetrievalResult(
                    id=str(point.id),
                    score=point.score,
                    title=title,
                    content=content,
                    vector_score=point.score,
                )

        results: List[RetrievalResult] = []
        for pt in exact_points:
            rid = str(pt.id)
            if rid not in seen_ids:
                seen_ids.add(rid)
                results.append(_make_result(pt))

        for pt in vector_points:
            rid = str(pt.id)
            if rid not in seen_ids:
                seen_ids.add(rid)
                results.append(_make_result(pt))

        return results[:k]

    async def retrieve_translation(
        self,
        query: str,
        top_k: int = 2,
    ) -> List[RetrievalResult]:
        """
        USP 翻译表检索（委托到 team domain，sub_domain="translation"）。
        """
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or 2,
            sub_domain="translation",
        )

    async def retrieve_usp_diagnosis(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        USP 诊断知识库检索（委托到 team domain，sub_domain="usp_product"）。
        """
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or 3,
            sub_domain="usp_product",
        )

    # ── 任务 Agent：平台参考文档检索 ───────────────────────────

    async def retrieve_platform_reference(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        平台部署/配置/代码排查参考文档检索（委托到 team domain，sub_domain="product"）。

        目标文件：platform_manual.md（技术架构）、engineer_guide.md（代码排查）。
        """
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or 3,
            sub_domain="product",
        )

    # ── 任务 Agent：历史工单方案检索 ───────────────────────────

    async def retrieve_task_resolutions(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        历史工单方案检索（委托到 project domain）。

        查询文本建议：problem_summary + hypotheses + fault_code + robot_type。
        """
        return await self.retrieve_domain(
            query, "project",
            top_k=top_k or 3,
        )

    async def ensure_task_resolutions_collection(self) -> str:
        """确保 project domain collection 存在，不存在则创建。

        Returns:
            collection 名称，创建失败返回空字符串。
        """
        from ai.config import get_active_collection_for, write_active_collection_for
        import time as _time

        tr_col = get_active_collection_for("project")
        if tr_col:
            try:
                client = await self._qdrant._ensure_client()
                if await self._to_thread(client.collection_exists, tr_col):
                    return tr_col
            except Exception:
                pass

        await self._ensure_clients()
        try:
            vec_dim = await self._embed_client.get_dimension()
            name = f"project_{_time.strftime('%Y%m%d_%H%M%S')}"
            client = await self._qdrant._ensure_client()
            from qdrant_client.models import Distance, VectorParams
            await self._to_thread(
                client.create_collection,
                collection_name=name,
                vectors_config=VectorParams(size=vec_dim, distance=Distance.COSINE),
            )
            write_active_collection_for("project", name)
            print(f"  [retrieval] Created project collection: {name}")
            return name
        except Exception as e:
            logger.error(f"创建 project 集合失败: {e}", exc_info=True)
            print(f"  [retrieval] Failed to create project collection: {e}")
            return ""

    async def index_task_resolution(
        self,
        task_id: str,
        title: str,
        root_cause: str,
        solution_steps: str,
        engineer_note: str = "",
        fault_code: str = "",
        robot_type: str = "",
        problem_summary: str = "",
    ) -> bool:
        """向量化并写入一条工单解决方案到 Qdrant（project domain）。"""
        from ai.config import get_active_collection_for
        import uuid

        col = get_active_collection_for("project")
        if not col:
            col = await self.ensure_task_resolutions_collection()
        if not col:
            return False

        await self._ensure_clients()
        if self._qdrant.is_unavailable:
            return False

        index_text = f"{title} {problem_summary} {root_cause} {solution_steps} {fault_code} {robot_type}"
        query_vector = await self._embed_client.embed(index_text)

        payload = {
            "task_id": task_id, "title": title,
            "problem_summary": problem_summary, "root_cause": root_cause,
            "solution_steps": solution_steps, "engineer_note": engineer_note,
            "fault_code": fault_code, "robot_type": robot_type,
            "domain": "project",
            "resolved_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        }

        return await self._qdrant.upsert_to_collection(
            collection_name=col,
            vectors=[query_vector.tolist()],
            ids=[str(uuid.uuid4())],
            payloads=[payload],
        )

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
