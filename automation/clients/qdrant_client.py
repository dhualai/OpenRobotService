from typing import Any, Dict, List, Optional

from automation.config import load_config
from automation.config.models import QdrantConfig
from automation.clients.base import BaseClient
from automation.utils.retry import sync_retry, RetryConfig
from automation.clients.exceptions import ClientConnectionError, QueryError


class QdrantClient(BaseClient):
    """Qdrant vector database client with retry, logging, and exception handling.

    Wraps qdrant-client for vector search operations.
    """

    def __init__(self, config: Optional[QdrantConfig] = None, retry_config: Optional[RetryConfig] = None):
        super().__init__(name="QdrantClient")
        self._cfg = config or load_config().qdrant
        self._retry_cfg = retry_config or RetryConfig()
        self._client: Any = None

    def __enter__(self) -> "QdrantClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def connect(self) -> None:
        try:
            from qdrant_client import QdrantClient as _QdrantClient
        except ImportError:
            raise ImportError("qdrant-client library is required. Install: pip install qdrant-client")

        self._client = _QdrantClient(
            host=self._cfg.host,
            port=self._cfg.port,
        )
        self._client.get_collections()
        self._connected = True
        self._log.info("Connected to Qdrant: %s:%s", self._cfg.host, self._cfg.port)

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._connected = False
            self._log.info("Qdrant client disconnected")

    def search(self, collection_name: str, query_vector: List[float], limit: int = 10) -> List[dict]:
        """Search for similar vectors in a collection.

        Args:
            collection_name: Name of the collection to search
            query_vector: Embedding vector for similarity search
            limit: Maximum number of results

        Raises:
            ClientConnectionError: If not connected
            QueryError: If the search fails
        """
        if not self._client:
            raise ClientConnectionError("Qdrant not connected", host=self._cfg.host, port=self._cfg.port)
        try:
            from qdrant_client.http import models
            results = self._search_with_retry(collection_name, query_vector, limit)
            return [self._serialize_scored_point(r) for r in results]
        except Exception as e:
            raise self._wrap_query_error(e, query=f"search {collection_name}")

    def upsert(self, collection_name: str, points: List[Dict[str, Any]]) -> int:
        """Insert or update points in a collection."""
        if not self._client:
            raise ClientConnectionError("Qdrant not connected", host=self._cfg.host, port=self._cfg.port)
        try:
            from qdrant_client.http import models
            qdrant_points = []
            for p in points:
                qdrant_points.append(models.PointStruct(
                    id=p["id"],
                    vector=p["vector"],
                    payload=p.get("payload", {}),
                ))
            result = self._upsert_with_retry(collection_name, qdrant_points)
            return result.status.value if hasattr(result, "status") else len(points)
        except Exception as e:
            raise self._wrap_query_error(e, query=f"upsert {collection_name}")

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists."""
        try:
            self._client.get_collection(collection_name)
            return True
        except Exception:
            return False

    @sync_retry()
    def _search_with_retry(self, collection_name: str, query_vector: List[float], limit: int = 10) -> list:
        return self._client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
        )

    @sync_retry()
    def _upsert_with_retry(self, collection_name: str, points: list) -> Any:
        return self._client.upsert(
            collection_name=collection_name,
            points=points,
        )

    @staticmethod
    def _serialize_scored_point(point: Any) -> dict:
        return {
            "id": point.id,
            "score": point.score,
            "payload": point.payload or {},
            "version": getattr(point, "version", None),
        }

