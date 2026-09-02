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
    source_file: str = ""
    # P2 验证状态透出（供 LLM 看到该历史方案是否经验证/被推翻）
    verified: str = "unknown"          # unknown|confirmed|rejected|recurred
    root_cause_type: str = ""
    error_codes: List[str] = field(default_factory=list)


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
        query_filter=None,
    ) -> List[Any]:
        """稀疏向量检索，可指定 collection（默认使用活跃集合）。"""
        if self.is_unavailable:
            return []
        # 本地模式同样支持稀疏检索（集合建库时带 sparse 索引，实测 query_points
        # using='sparse' 正常返回命中）——旧守卫「本地模式不支持」是过时误判，
        # 导致 BM25 关键词路从未生效（RRF 融合长期只有稠密路一腿）。
        client = await self._ensure_client()
        col = collection_name or self.collection_name
        try:
            # qdrant_client >=1.10: sparse vector 需要 SparseVector 对象
            indices = sorted(sparse_vector.keys())
            values = [sparse_vector[i] for i in indices]
            sv = SparseVector(indices=indices, values=values)

            # qdrant_client >=1.18: search() → query_points()
            # 稀疏检索走倒排索引，不需要（也不应传）HNSW SearchParams
            result = await self._to_thread(
                client.query_points,
                collection_name=col,
                query=sv,
                using="sparse",
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
                query_filter=query_filter,
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

        CPU 推理瓶颈：bge-reranker-v2-m3 在 CPU 上耗时 ∝ 对数×文本长度，
        12对×400字实测 ~4s，拖垮回答体验。收窄到 8 对×220 字（配合
        max_length=256），~1.5s 内完成；池召回问题靠上游分池保证。
        """
        _MAX_RERANK_CANDIDATES = 8  # 精排候选上限（CPU 耗时控制）
        if not self._reranker or len(results) <= top_k:
            return results[:top_k]

        capped = results[:_MAX_RERANK_CANDIDATES]
        # rerank 文本 = 标题末段（小节名）+ 正文前段：与 embed_text 口径对齐
        # （dense 检索的嵌入文本含标题，rerank 只喂裸 content 会让配置类块
        # （YAML/参数表，正文语义弱）在精排里被自然语言块系统性压低——
        # 转弯速度案例实锤：标题带「转弯速度」的配置块进池后被排到 6 名外）
        # 截 220 字：标题在前，相关性判断主要靠前段，长文只费 CPU 不涨分。
        docs = [f"{(r.title or '').rsplit(' / ', 1)[-1]}\n{r.content}"[:220]
                for r in capped]
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

        # 精排分写回 score：rerank 已改变顺序，若 score 仍是 RRF 分则与顺序
        # 脱钩——下游（如 ticket_resolutions 的 verified 加权重排、测试断言
        # 降序）都假设 score 单调对应最终顺序
        scored = sorted(zip(capped, scores), key=lambda x: x[1], reverse=True)
        for r, s in scored[:top_k]:
            r.score = float(s)
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

    def _point_to_result(self, point: Any, score: float,
                         vector_score: float = 0.0, sparse_score: float = 0.0) -> RetrievalResult:
        """Qdrant point → RetrievalResult（payload 提取复用）。"""
        pl = point.payload or {}
        return RetrievalResult(
            id=str(point.id),
            score=score,
            title=pl.get("title", ""),
            content=pl.get("content", ""),
            vector_score=vector_score,
            sparse_score=sparse_score,
            images=pl.get("images", []),
            sub_domain=pl.get("sub_domain", ""),
            domain=pl.get("domain", ""),
            source_file=pl.get("source_file", ""),
            verified=pl.get("verified", "unknown") or "unknown",
            root_cause_type=pl.get("root_cause_type", "") or "",
            error_codes=pl.get("error_codes", []) or [],
        )

    async def retrieve_domain_dual(
        self,
        query: str,
        domain: str,
        top_k: int = 8,
        query_filter=None,
    ) -> Tuple[List[RetrievalResult], List[RetrievalResult]]:
        """双路原始检索：返回 (稠密结果, 稀疏结果)，不融合。
        用于「两路各自保送 → 合并候选池 → cross-encoder 精排」的策略：
        RRF 早融合会把只被一路命中的文档挤出候选（锁区文档稠密路第4名，
        RRF 后掉出 top12 精排池，cross-encoder 永远看不到它）。
        """
        from ai.config import get_active_collection_for

        col = get_active_collection_for(domain)
        if not col:
            logger.warning(f"[dual] {domain} 域活跃集合指针为空，双路检索跳过")
            return [], []
        logger.info(f"[dual] {domain} 域检索集合: {col}")
        await self._ensure_clients()
        if self._qdrant.is_unavailable:
            logger.warning(f"[dual] {domain} 域 Qdrant 不可用，双路检索跳过")
            return [], []

        query_vector = await self._embed_client.embed(query)
        bm25_sparse = self._generate_bm25_sparse(query)

        dense_task = self._qdrant.search_dense(
            query_vector.tolist(),
            top_k=top_k,
            collection_name=col,
            query_filter=query_filter,
        )
        # 稀疏路同样带过滤条件（此前 search_sparse 漏传 query_filter，
        # sub_domain 场景会混入其它子域文档）
        sparse_task = self._qdrant.search_sparse(
            bm25_sparse,
            top_k=top_k,
            collection_name=col,
            query_filter=query_filter,
        ) if bm25_sparse else None

        if sparse_task is None:
            dense_res = await dense_task
            return ([self._point_to_result(p, p.score, vector_score=p.score)
                     for p in dense_res], [])

        dense_res, sparse_list = await asyncio.gather(dense_task, sparse_task)
        if isinstance(sparse_list, BaseException):
            sparse_list = []
        if not isinstance(sparse_list, list):
            sparse_list = []
        return (
            [self._point_to_result(p, p.score, vector_score=p.score) for p in dense_res],
            [self._point_to_result(p, p.score, sparse_score=p.score) for p in sparse_list],
        )

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
                    "verified": "unknown", "root_cause_type": "", "error_codes": [],
                }
            doc_scores[doc_id]["dense"] = 1.0 / (rrf_k + rank + 1)
            doc_scores[doc_id]["vector_score"] = point.score
            if point.payload:
                doc_scores[doc_id]["title"] = point.payload.get("title", "")
                doc_scores[doc_id]["content"] = point.payload.get("content", "")
                doc_scores[doc_id]["images"] = point.payload.get("images", [])
                doc_scores[doc_id]["sub_domain"] = point.payload.get("sub_domain", "")
                doc_scores[doc_id]["domain"] = point.payload.get("domain", "")
                doc_scores[doc_id]["verified"] = point.payload.get("verified", "unknown") or "unknown"
                doc_scores[doc_id]["root_cause_type"] = point.payload.get("root_cause_type", "") or ""
                doc_scores[doc_id]["error_codes"] = point.payload.get("error_codes", []) or []

        for rank, point in enumerate(sparse_results):
            doc_id = str(point.id)
            if doc_id not in doc_scores:
                doc_scores[doc_id] = {
                    "dense": 0.0, "sparse": 0.0,
                    "title": "", "content": "", "images": [],
                    "vector_score": 0.0, "sparse_score": 0.0,
                    "sub_domain": "", "domain": "",
                    "verified": "unknown", "root_cause_type": "", "error_codes": [],
                }
            doc_scores[doc_id]["sparse"] = 1.0 / (rrf_k + rank + 1)
            doc_scores[doc_id]["sparse_score"] = point.score
            if point.payload and not doc_scores[doc_id]["title"]:
                doc_scores[doc_id]["title"] = point.payload.get("title", "")
                doc_scores[doc_id]["content"] = point.payload.get("content", "")
                doc_scores[doc_id]["images"] = point.payload.get("images", [])
                doc_scores[doc_id]["sub_domain"] = point.payload.get("sub_domain", "")
                doc_scores[doc_id]["domain"] = point.payload.get("domain", "")
                doc_scores[doc_id]["verified"] = point.payload.get("verified", "unknown") or "unknown"
                doc_scores[doc_id]["root_cause_type"] = point.payload.get("root_cause_type", "") or ""
                doc_scores[doc_id]["error_codes"] = point.payload.get("error_codes", []) or []

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
                verified=scores.get("verified", "unknown") or "unknown",
                root_cause_type=scores.get("root_cause_type", "") or "",
                error_codes=scores.get("error_codes", []) or [],
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
        _rerank_margin = min(k + 4, 8)
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
        skip_rerank: bool = False,
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
        # ⚠️ sparse 不传 query_filter（有意为之）：本地 qdrant（local 模式）的
        # payload filter 与 MatchAny 组合返回空——retrieve_faq 等带 MatchAny 的
        # 检索在本地 dense 路恒 0，全靠 sparse 裸路撑住测试。生产 server 版
        # qdrant filter 正常；sparse 补过滤是正确性改进但需在 server 版验证后
        # 再上（本地无法验证）。
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
        if self._reranker and not skip_rerank and len(results) > top_k:
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
                # 目录改名前后的子域名并存（team/faq → team/usp/faq → team/USP/faq）
                match=MatchAny(any=["faq", "usp_faq", "usp/faq", "USP/faq"]),
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
        问题排查树/诊断参考检索（team domain，子域 diagnosis）。
        """
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        diag_filter = Filter(
            must=[FieldCondition(
                key="sub_domain",
                # 目录改名前后的子域名并存
                # （diagnosis → usp/diagnosis → USP/diagnosis → USP/troubleshooting）
                match=MatchAny(any=[
                    "diagnosis", "usp/diagnosis", "USP/diagnosis", "USP/troubleshooting",
                ]),
            )]
        )
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or 3,
            query_filter=diag_filter,
        )

    @staticmethod
    def _extract_error_codes(query: str) -> List[str]:
        r"""从查询中提取可能的车端错误码（3~5 位数字）。
        不能用 \b —— Python 3 re.UNICODE 默认开启，中文被视作 \w。
        数字前紧贴字母时不抽（XS1152 是型号不是码；否则 1152 会当错误码
        精确匹配，把无关错误码顶到最前，真正的 IO 手册反被挤出）。"""
        return re.findall(r'(?<![A-Za-z\d])(\d{3,5})(?!\d)', query)

    async def retrieve_cheduan(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[RetrievalResult]:
        """
        车端错误码检索（委托到 company domain，sub_domain="vehicle_errors"）。

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
                must=[FieldCondition(key="sub_domain",
                                     match=MatchValue(value="vehicle_errors"))]
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
                    # 消费端（pipeline._cheduan_found）靠 sub_domain 判断
                    # 是否命中错误码库，缺失会让已命中的码被错插「未找到」提示
                    sub_domain=payload.get("sub_domain", ""),
                    domain=payload.get("domain", ""),
                )
            else:
                return RetrievalResult(
                    id=str(point.id),
                    score=point.score,
                    title=title,
                    content=content,
                    vector_score=point.score,
                    sub_domain=payload.get("sub_domain", ""),
                    domain=payload.get("domain", ""),
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

    # ────────────────────────────────────────────────────────────
    # 完整知识库检索（三路域双路检索 + 平衡选取 + 格式化）
    # 复用 AiDiagnosisPlatform 的成熟检索策略，供 AiTaskPlatform 的
    # retrieve_kb 能力调用，让 @U老师 讨论也能命中完整知识库
    # （team 操作手册/FAQ/排查 / company 产品/车端错误码/VDA5050 /
    #  industry 行业标准/导航规范）。
    # ────────────────────────────────────────────────────────────
    async def retrieve_ai_kb(
        self,
        query: str,
        top_k: int = 6,
        domains: Optional[Tuple[str, ...]] = None,
    ) -> str:
        """完整知识库三路检索，返回已格式化的文本（供 LLM 参考）。

        Args:
            query: 检索问题/关键词
            top_k: 送 prompt 的最大条数（默认 6）
            domains: 要检索的域（默认 team/company/industry）

        Returns:
            格式化知识库文本；无结果时返回提示"知识库暂无匹配"。
        """
        domains = domains or ("team", "company", "industry")
        import time as _time
        t0 = _time.perf_counter()

        # sub_domain → 标签映射
        _sub_labels = {
            "platform": "🎫 服务号", "yaorenba": "🎫 服务号", "ORS": "🎫 服务号",
            "faq": "📋 FAQ", "usp_faq": "📋 FAQ", "usp/faq": "📋 FAQ",
            "USP/faq": "📋 FAQ",
            "cheduan_errors": "🚗 车端", "cheduan_implementation": "🚗 车端",
            "cheduan_calibration": "🚗 车端", "cheduan_io": "🚗 车端",
            "motion_control": "🚗 车端",
            "vehicle_errors": "🚗 车端", "vehicle_implementation": "🚗 车端",
            "vehicle_calibration": "🚗 车端", "vehicle_io": "🚗 车端",
            "vehicle_motion": "🚗 车端",
            "translation": "🌐 翻译", "USP/translation": "🌐 翻译",
            "diagnosis": "🏭 诊断", "usp/diagnosis": "🏭 诊断",
            "USP/diagnosis": "🏭 诊断", "USP/troubleshooting": "🏭 排查树",
            "usp_manual": "📖 手册", "usp/manual": "📖 手册", "USP/manual": "📖 手册",
            "usp_cards": "🔍 诊断卡",
            "usp/overview": "📘 模块文档", "USP/overview": "📘 模块文档",
            "usp/error_codes": "🚨 平台错误码", "USP/error_codes": "🚨 平台错误码",
            "usp/ui_pages": "🧭 页面导航", "USP/ui_pages": "🧭 页面导航",
            "usp/terminology": "🔤 术语表", "USP/terminology": "🔤 术语表",
            "product_catalog": "🏢 产品", "vda5050_protocol": "🏢 协议",
            "navigation": "📐 导航", "standards": "📐 标准",
        }

        def _label(r) -> str:
            return _sub_labels.get(r.sub_domain, f"📄 {r.sub_domain or '知识库'}")

        def _sec_key(r):
            _t = r.title or ""
            _sec = re.split(r" [>/·] ", _t, maxsplit=1)[0].strip() if _t else ""
            return (r.source_file or r.sub_domain or "", _sec)

        async def _one(domain: str, dom_top_k: int):
            try:
                dense_res, sparse_res = await asyncio.wait_for(
                    self.retrieve_domain_dual(query, domain, top_k=8),
                    timeout=15.0,
                )
                return list(dense_res)[:dom_top_k], list(sparse_res)[:dom_top_k]
            except Exception as e:
                logger.warning(f"[retrieve_ai_kb] {domain} 域双路检索失败: "
                               f"{type(e).__name__}: {str(e)[:300]}")
                return [], []

        _weights = {"team": 5, "company": 4, "industry": 3}
        _tasks = [asyncio.create_task(_one(d, _weights.get(d, 4))) for d in domains]
        _gathered = await asyncio.gather(*_tasks, return_exceptions=True)

        # 合并候选池（稠密+稀疏各自保送，避免 RRF 早融合挤掉只被一路命中的文档）
        _dense_part: list = []
        _sparse_part: list = []
        _seen_ids: set = set()

        def _push(r: RetrievalResult) -> None:
            if r.id in _seen_ids:
                return
            _seen_ids.add(r.id)
            if getattr(r, "sparse_score", 0):
                _sparse_part.append(r)
            else:
                _dense_part.append(r)

        for _g in _gathered:
            if isinstance(_g, BaseException):
                continue
            _dn, _sp = _g
            for r in _dn:
                _push(r)
            for r in _sp:
                _push(r)

        _dense_part.sort(key=lambda r: getattr(r, "vector_score", 0) or 0, reverse=True)
        _sparse_part.sort(key=lambda r: getattr(r, "sparse_score", 0) or 0, reverse=True)

        # 车端错误码精确匹配：query 含数字错误码时优先精确命中
        _cheduan_exact: list = []
        _query_codes = self._extract_error_codes(query)
        if _query_codes:
            try:
                _cheduan_exact = await asyncio.wait_for(
                    self.retrieve_cheduan(query, top_k=3),
                    timeout=10.0,
                )
            except Exception:
                _cheduan_exact = []

        # 平衡选取（同节最多 2 条）
        _final: list = []
        _final_tags: list = []
        _fs: set = set()
        _sec_counts: dict = {}

        def _take(queue: list, n_slots: int, tag: str) -> None:
            _taken = 0
            for r in queue:
                if _taken >= n_slots:
                    break
                if r.id in _fs:
                    continue
                _k = _sec_key(r)
                if _sec_counts.get(_k, 0) >= 2:
                    continue
                _fs.add(r.id)
                _sec_counts[_k] = _sec_counts.get(_k, 0) + 1
                _final.append(r)
                _final_tags.append(tag)
                _taken += 1

        for r in _cheduan_exact:
            if r.id not in _fs:
                _fs.add(r.id)
                _final.append(r)
                _final_tags.append("码")
        # 精排重接（0901，与诊断主链路 _retrieve_with_context 同款）：
        # 密4+疏4 平衡截断 → cross-encoder 全池重排 → 取剩余名额。
        # 取代密4+疏2 双池直选；reranker 失败时降级为候选原序。
        _balanced, _seen_bal = [], set()
        for r in _dense_part[:4] + _sparse_part[:4]:
            if r.id not in _seen_bal:
                _seen_bal.add(r.id)
                _balanced.append(r)
        _reranked = await self._rerank_results(query, _balanced, top_k=top_k)
        _take(_reranked, top_k - len(_final), "精")

        docs: list = []
        idx = 1
        for _ri, r in enumerate(_final[:top_k]):
            content = (r.content or "").strip()
            if not content:
                continue
            content = content[:800]
            title = f"（{r.title}）" if r.title else ""
            docs.append(f"---\n{_label(r)} {idx}{title}：\n{content}\n---")
            idx += 1

        logger.info(f"[retrieve_ai_kb] 域召回完成: 密集{len(_dense_part)} 稀疏{len(_sparse_part)} "
                    f"车端{len(_cheduan_exact)} 送prompt{len(docs)} "
                    f"耗时{round((_time.perf_counter() - t0) * 1000)}ms: query={query[:50]}")
        if not docs:
            return "（知识库暂无匹配文档。若用户问题属于知识问答，请如实告知当前手册未覆盖，不要编造答案。）"
        return "\n".join(docs)

    async def retrieve_translation(
        self,
        query: str,
        top_k: int = 2,
    ) -> List[RetrievalResult]:
        """
        USP 翻译表检索（team domain，子域 translation）。
        """
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        trans_filter = Filter(
            must=[FieldCondition(
                key="sub_domain",
                # 目录改名前后子域名并存（translation → USP/translation）
                match=MatchAny(any=["translation", "USP/translation"]),
            )]
        )
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or 2,
            query_filter=trans_filter,
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
        平台部署/配置/代码排查参考文档检索（team domain）。

        目标文件：USP/manual/product.md（技术架构/代码排查，随手册子域入库）。
        """
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        ref_filter = Filter(
            must=[FieldCondition(
                key="sub_domain",
                # product.md 现随手册入库（sub_domain=USP/manual）；
                # 保留历史结构子域名做兼容
                match=MatchAny(any=["product", "usp_product", "usp/manual", "USP/manual"]),
            )]
        )
        return await self.retrieve_domain(
            query, "team",
            top_k=top_k or 3,
            query_filter=ref_filter,
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
        P2：按验证状态调整权重（经验证 confirmed 提权、被推翻 rejected/复发 recurred 降权），
        让"可信"的方案更靠前，避免"看似结案其实错"的样本误导排查。
        """
        # 0828 用户拍板：工单解决经验归 company 域（公司级跨项目服务资产，
        # 诊断默认检索域含 company），sub_domain=ticket_resolutions 与手册文档
        # 隔离——历史工单作为独立检索路按子域过滤，不与文档抢 top_k。
        results = await self.retrieve_domain(
            query, "company",
            top_k=top_k or 3,
            sub_domain="ticket_resolutions",
        )
        # P2 verified 权重：confirmed×1.15，recurred×0.85，rejected×0.7，其余×1.0
        _weight = {
            "confirmed": 1.15,
            "recurred": 0.85,
            "rejected": 0.70,
        }
        for r in results:
            w = _weight.get((getattr(r, "verified", "") or "unknown"), 1.0)
            if w != 1.0:
                r.score = r.score * w
        results.sort(key=lambda x: x.score, reverse=True)
        return results[: top_k or 3]

    async def ensure_task_resolutions_collection(self) -> str:
        """确保 project domain collection 存在，不存在则创建。

        Returns:
            collection 名称，创建失败返回空字符串。
        """
        from ai.config import get_active_collection_for, write_active_collection_for
        import time as _time

        tr_col = get_active_collection_for("company")
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
            name = f"company_{_time.strftime('%Y%m%d_%H%M%S')}"
            client = await self._qdrant._ensure_client()
            # 与主入库链路同 schema（ai/ingestion/base.py）：命名向量
            # dense + sparse——否则 BM25 稀疏检索路在建出的集合上永远空转
            from qdrant_client.models import (Distance, VectorParams,
                                              SparseVectorParams, SparseIndexParams)
            await self._to_thread(
                client.create_collection,
                collection_name=name,
                vectors_config={
                    "dense": VectorParams(size=vec_dim, distance=Distance.COSINE),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(on_disk=False)),
                },
            )
            write_active_collection_for("company", name)
            print(f"  [retrieval] Created company collection: {name}")
            return name
        except Exception as e:
            logger.error(f"创建 company 集合失败: {e}", exc_info=True)
            print(f"  [retrieval] Failed to create company collection: {e}")
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
        # P1 结构化根因（可选，缺省用 safe 默认，兼容旧调用/旧数据）
        root_cause_type: str = "unknown",
        error_codes: "Optional[List[str]]" = None,
        severity: str = "unknown",
        is_common_bug: bool = False,
        verified: str = "unknown",
        extra_payload: "Optional[dict]" = None,
    ) -> bool:
        """向量化并写入一条工单解决方案到 Qdrant（project domain）。

        extra_payload：附加 payload 字段（如评论原文、解决人），仅存 payload
        供生成答案时引用，不参与向量化。"""
        from ai.config import get_active_collection_for
        import uuid

        col = get_active_collection_for("company")
        if not col:
            col = await self.ensure_task_resolutions_collection()
        if not col:
            return False

        await self._ensure_clients()
        if self._qdrant.is_unavailable:
            return False

        # 检索文本只放「问题侧」信号（0828 用户拍板）：查询是问题描述语义，
        # 索引侧对齐问题侧；解决步骤是答案不是问题，进向量只会稀释——全文在
        # payload 里供生成答案时引用。根因短语保留（「充电桩通信板故障」是
        # 同类问题的强召回信号）。
        index_text = f"{title} {problem_summary} {root_cause} {fault_code} {robot_type}"
        query_vector = await self._embed_client.embed(index_text)

        payload = {
            "task_id": task_id, "title": title,
            "problem_summary": problem_summary, "root_cause": root_cause,
            "solution_steps": solution_steps, "engineer_note": engineer_note,
            "fault_code": fault_code, "robot_type": robot_type,
            "domain": "company", "sub_domain": "ticket_resolutions",
            # content 是消费侧 RetrievalResult.content 的映射源
            # （主 KB chunk 文本同键）——缺它检索结果正文为空
            "content": (
                f"问题：{problem_summary}\n根因：{root_cause}\n"
                f"解决：{solution_steps}"
                + (f"\n故障码：{fault_code}" if fault_code else "")
                + (f"\n车型：{robot_type}" if robot_type else "")),
            "resolved_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            # P1 结构化根因（供查询过滤 + 验证回填）
            "root_cause_type": root_cause_type or "unknown",
            "error_codes": list(error_codes or []),
            "severity": severity or "unknown",
            "is_common_bug": bool(is_common_bug),
            "verified": verified or "unknown",
        }
        if extra_payload:
            payload.update(extra_payload)

        # 确定性 ID（uuid5 of task_id）：同一工单重复索引覆盖同一向量点，
        # 幂等——回填与增量 worker 并存时不会产生重复点（旧 uuid4 每次
        # upsert 都是新点，重复索引会膨胀集合）。
        # 命名双向量（与主入库链路 ai/ingestion/base.py 同 schema）：
        # dense 语义 + sparse BM25——检索侧 RRF 融合的稀疏路才有料。
        _point_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   f"yaorenba:task_resolution:{task_id}"))
        try:
            from qdrant_client.models import PointStruct, SparseVector
            from ai.core.retrieval import generate_bm25_sparse
            _sparse = generate_bm25_sparse(index_text)
            _s_idx = sorted(_sparse.keys())
            point = PointStruct(
                id=_point_id,
                vector={
                    "dense": query_vector.tolist(),
                    "sparse": SparseVector(
                        indices=_s_idx, values=[_sparse[i] for i in _s_idx]),
                },
                payload=payload,
            )
            client = await self._qdrant._ensure_client()
            await self._qdrant._to_thread(
                client.upsert, collection_name=col, points=[point])
            return True
        except Exception as e:
            logger.error(f"[retrieval] 工单方案写入失败: task={task_id}, err={e}",
                         exc_info=True)
            return False

    # P2 验证状态回填：按 task_id 更新该工单方案点的 verified 字段
    async def update_task_resolution_verified(
        self,
        task_id: str,
        verified: str,
    ) -> bool:
        """按 task_id 更新一条工单方案的 verified 字段（P2 验证回填）。

        在 project domain 集合里 scroll 查找 task_id 匹配的点，set_payload 更新 verified。
        找不到返回 False，不影响主流程。
        """
        from ai.config import get_active_collection_for

        if verified not in ("confirmed", "rejected", "recurred", "unknown"):
            return False
        col = get_active_collection_for("company")
        if not col:
            return False
        await self._ensure_clients()
        if self._qdrant.is_unavailable:
            return False

        from qdrant_client.models import Filter, FieldCondition, MatchValue
        client = await self._qdrant._ensure_client()
        try:
            flt = Filter(
                must=[
                    FieldCondition(
                        key="task_id",
                        match=MatchValue(value=str(task_id)),
                    )
                ]
            )
            # 分页 scroll 找到匹配点（limit 64 足够覆盖单工单的重复索引）
            matched = await self._to_thread(
                client.scroll,
                collection_name=col,
                scroll_filter=flt,
                limit=64,
                with_payload=False,
                with_vectors=False,
            )
            points, _next = matched
            if not points:
                return False
            ids = [p.id for p in points]
            await self._to_thread(
                client.set_payload,
                collection_name=col,
                payload={"verified": verified},
                points=ids,
            )
            logger.info(f"[retrieval] 已回填 verified={verified}: task_id={task_id} 覆盖 {len(ids)} 点")
            return True
        except Exception as e:
            logger.warning(f"[retrieval] update verified 失败 task_id={task_id}: {e}")
            return False

    # ── 派单模块（Assigner）：历史工单向量库 dispatch_history ──

    async def ensure_dispatch_history_collection(self) -> str:
        """确保 dispatch domain collection（派单历史工单）存在，不存在则创建。

        Returns:
            collection 名称，创建失败返回空字符串。
        """
        from ai.config import get_active_collection_for, write_active_collection_for
        import time as _time

        col = get_active_collection_for("dispatch")
        if col:
            try:
                client = await self._qdrant._ensure_client()
                if await self._qdrant._to_thread(client.collection_exists, col):
                    return col
            except Exception:
                pass

        await self._ensure_clients()
        try:
            vec_dim = await self._embed_client.get_dimension()
            name = f"dispatch_{_time.strftime('%Y%m%d_%H%M%S')}"
            client = await self._qdrant._ensure_client()
            from qdrant_client.models import Distance, VectorParams
            await self._qdrant._to_thread(
                client.create_collection,
                collection_name=name,
                vectors_config=VectorParams(size=vec_dim, distance=Distance.COSINE),
            )
            write_active_collection_for("dispatch", name)
            print(f"  [retrieval] Created dispatch collection: {name}")
            return name
        except Exception as e:
            logger.error(f"创建 dispatch 集合失败: {e}", exc_info=True)
            print(f"  [retrieval] Failed to create dispatch collection: {e}")
            return ""

    async def index_dispatch_history(
        self,
        *,
        engineer_id: str,
        title: str,
        description: str,
        modules: Optional[List[str]] = None,
        task_type: str = "problem",
        fault_code: str = "",
        robot_type: str = "",
        closed_at: Optional[str] = None,
    ) -> bool:
        """向量化并写入一条派单历史工单到 Qdrant（dispatch domain）。

        Payload 特别带上 engineer_id（解决人），供 L3-A 路按人聚合。
        查询/向量文本 = 标题+描述+故障码+车型（与派单召回语义一致）。
        """
        from ai.config import get_active_collection_for
        import uuid

        col = get_active_collection_for("dispatch")
        if not col:
            col = await self.ensure_dispatch_history_collection()
        if not col:
            return False

        await self._ensure_clients()
        if self._qdrant.is_unavailable:
            return False

        index_text = " ".join(filter(None, [
            title, description, robot_type, fault_code,
        ]))
        query_vector = await self._embed_client.embed(index_text)

        payload = {
            "engineer_id": engineer_id,     # 解决人（核心）
            "title": title,
            "description": description,
            "modules": modules or [],       # 问题域标签（模块）
            "task_type": task_type,
            "fault_code": fault_code,
            "robot_type": robot_type,
            "closed_at": closed_at or "",
            "domain": "dispatch",
        }

        return await self._qdrant.upsert_to_collection(
            collection_name=col,
            vectors=[query_vector.tolist()],
            ids=[str(uuid.uuid4())],
            payloads=[payload],
        )

    async def retrieve_dispatch_history(
        self,
        query: str,
        top_k: int = 10,
        score_threshold: Optional[float] = None,
    ) -> List[dict]:
        """检索派单相似历史工单（dispatch domain）。

        返回原始 Qdrant points（含 payload.engineer_id），供 L3-A 路按解决人聚合。
        与 retrieve_task_resolutions 不同：这里返回原始 point 而非 RetrievalResult，
        因为 L3 需要 engineer_id（RetrievalResult 不暴露 payload 自定义字段）。

        Returns:
            每个元素 = {"engineer_id", "score", "payload", ...}（无结果返回空列表）。
        """
        from ai.config import get_active_collection_for

        col = get_active_collection_for("dispatch")
        if not col:
            return []

        await self._ensure_clients()
        if self._qdrant.is_unavailable:
            return []

        try:
            qe = await self._embed_client.embed(query)
            points = await self._qdrant.search_dense(
                qe.tolist(),
                top_k=top_k,
                score_threshold=score_threshold,
                collection_name=col,
            )
            results = []
            for p in points:
                pl = p.payload or {}
                results.append({
                    "engineer_id": pl.get("engineer_id", ""),
                    "score": float(p.score) if p.score is not None else 0.0,
                    "title": pl.get("title", ""),
                    "description": pl.get("description", ""),
                    "modules": pl.get("modules", []),
                    "task_type": pl.get("task_type", ""),
                    "fault_code": pl.get("fault_code", ""),
                    "robot_type": pl.get("robot_type", ""),
                    "closed_at": pl.get("closed_at", ""),
                })
            return results
        except Exception as e:
            logger.warning(f"[retrieval] 派单历史检索失败: {e}")
            return []

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
