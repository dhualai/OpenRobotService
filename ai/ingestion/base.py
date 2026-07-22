"""
统一入库框架 — BaseIngester

所有知识库入库脚本的公共基类。每个子类只需实现：
  - parse() → List[T]    从源文件提取结构化数据
  - to_chunk(T) → Chunk  将一条数据转为可入库的 chunk

框架自动处理：embedding、Qdrant 写入、集合管理、指针切换、旧集合清理。

使用示例：
    class MyIngester(BaseIngester[MyEntry]):
        source_paths = [Path("docs/my_data.xlsx")]
        collection_prefix = "my_kb"
        pointer_writer = staticmethod(config._write_active_my_kb_collection)
        pointer_reader = staticmethod(config.get_active_my_kb_collection)

        def parse(self) -> List[MyEntry]:
            ...

        def to_chunk(self, entry: MyEntry) -> "Chunk":
            ...
"""
import hashlib
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Generic, TypeVar, Callable

from ai.core.logging import get_logger

logger = get_logger(__name__)

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dataclasses import dataclass, field

# ── 通用 Chunk 格式 ────────────────────────────────────────────

@dataclass
class Chunk:
    """入库的最小单元——一个向量 + 一个 payload"""
    id: str                        # 稳定 hash ID（用于 upsert 去重）
    text: str                      # 向量化文本
    payload: Dict[str, Any] = field(default_factory=dict)

# ── 类型变量 ───────────────────────────────────────────────────

T = TypeVar("T")


class BaseIngester(ABC, Generic[T]):
    """
    知识库入库基类。

    子类必须提供：
      - source_paths: 源文件路径列表
      - collection_prefix: Qdrant 集合前缀（如 "cheduan", "faq_docs"）
      - pointer_reader / pointer_writer: 活跃集合指针的读写函数
      - parse() → List[T]
      - to_chunk(entry: T) → Chunk

    可选覆盖：
      - rebuild: bool = True  → auto_ingest 时是否重建集合（默认 True）
      - collection_type: str  → 用于 registry 匹配的集合类型标签
    """

    # ── 子类必须定义 ──
    source_paths: List[Path] = []
    collection_prefix: str = ""
    pointer_reader: Callable[[], str] = lambda: ""
    pointer_writer: Callable[[str], None] = lambda _: None

    # ── 子类可选覆盖 ──
    rebuild: bool = True           # False = 追加到现有集合
    collection_type: str = ""       # 用于 registry（operation/faq/troubleshooting/cheduan/translation）

    # ── 运行时可设置 ──
    verbose: bool = True

    # ================================================================
    # 抽象方法
    # ================================================================

    @abstractmethod
    def parse(self) -> List[T]:
        """从源文件提取结构化数据。子类必须实现。"""
        ...

    @abstractmethod
    def to_chunk(self, entry: T) -> "Chunk":
        """将一条结构化数据转为 Chunk（id + text + payload）。子类必须实现。"""
        ...

    # ================================================================
    # 可选覆盖
    # ================================================================

    def get_source_label(self) -> str:
        """返回源文件标签（用于日志和 payload.source）"""
        if self.source_paths:
            return self.source_paths[0].name
        return self.collection_prefix

    def validate_source_files(self) -> bool:
        """检查源文件是否存在。不存在时打印警告并返回 False。"""
        missing = [p for p in self.source_paths if not p.exists()]
        if missing:
            for p in missing:
                self._log(f"[WARN] 源文件不存在: {p}")
            return False
        return True

    # ================================================================
    # 通用入库流程（子类不需要覆盖）
    # ================================================================

    async def embed_and_upsert(
        self,
        chunks: List[Chunk],
        collection_name: str,
        client=None,  # 可复用外部传入的 client（本地模式避免多实例冲突）
    ) -> Dict[str, Any]:
        """
        向量化 + 写入 Qdrant。所有子类共用此方法。

        Args:
            chunks: 待入库的 chunk 列表
            collection_name: 目标 Qdrant 集合名
            client: 可选，复用外部 QdrantClient（本地文件模式推荐传入）
        """
        from ai.core.embed import get_embed_client
        from ai.config import get_ai_config
        from qdrant_client.models import PointStruct, Distance, VectorParams

        config = get_ai_config()
        embed_client = await get_embed_client()
        _own_client = client is None

        # 日志
        if config.qdrant_local_path:
            local = Path(config.qdrant_local_path)
            if not local.is_absolute():
                local = _project_root / local
            self._log(f"[INGEST] {len(chunks)} chunks -> Qdrant (local: {local})")
        else:
            self._log(f"[INGEST] {len(chunks)} chunks -> Qdrant ({config.qdrant_host}:{config.qdrant_port})")
        self._log(f"   Collection: {collection_name}")

        # 1. 向量化
        texts = [c.text for c in chunks]
        vectors = await embed_client.embed_batch(texts)
        dim = vectors[0].shape[-1]
        self._log(f"   dim={dim}")

        # 2. 连接 Qdrant
        if client is None:
            client = self._make_qdrant_client(config)

        # 3. 重建（可选）
        if self.rebuild and client.collection_exists(collection_name):
            self._log(f"[DROP] 删除旧集合 '{collection_name}'...")
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass

        # 4. 确保集合存在
        if not client.collection_exists(collection_name):
            self._log(f"[CREATE] 创建集合 '{collection_name}'...")
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

        # 5. 写入
        self._log(f"[WRITE] upserting {len(chunks)} points...")
        points = [
            PointStruct(id=c.id, vector=v.tolist(), payload=c.payload)
            for c, v in zip(chunks, vectors)
        ]

        batch_size = 50
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            client.upsert(collection_name=collection_name, points=batch)
            if self.verbose:
                print(f"   [{min(i + batch_size, len(points))}/{len(points)}]")

        if _own_client:
            try:
                client.close()
            except Exception:
                pass

        return {
            "status": "ok",
            "entries": len(chunks),
            "dimension": dim,
            "collection": collection_name,
        }

    async def auto_ingest(self, client=None) -> bool:
        """
        自动入库入口（被 run.py lifespan 调用）。
        流程：验证源文件 → 解析 → 构建 chunk → 入库 → 写指针 → 清理旧集合。

        Args:
            client: 可选，复用外部 QdrantClient（批量入库时避免频繁创建/释放）
        """
        label = self.get_source_label()

        if not self.validate_source_files():
            self._log(f"[SKIP] {label}: 源文件缺失，跳过")
            return False

        # 1. 解析
        try:
            entries = self.parse()
        except Exception as e:
            logger.error(f"入库解析失败: {label}, error={e}", exc_info=True)
            self._log(f"[ERR] {label}: 解析步骤失败 — {e}")
            import traceback
            traceback.print_exc()
            return False
        self._log(f"[{label}] 解析到 {len(entries)} 条数据")

        if not entries:
            self._log(f"[WARN] {label}: 未解析到任何数据")
            return False

        # 2. 构建 chunk
        try:
            chunks = [self.to_chunk(e) for e in entries]
        except Exception as e:
            logger.error(f"入库 chunk 构建失败: {label}, error={e}", exc_info=True)
            self._log(f"[ERR] {label}: 构建 chunk 失败 — {e}")
            import traceback
            traceback.print_exc()
            return False
        self._log(f"[{label}] 构建 chunk 完成: {len(chunks)} 条")

        # 3. 确定集合名
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.rebuild:
            collection_name = f"{self.collection_prefix}_{ts}"
        else:
            collection_name = self.pointer_reader() or f"{self.collection_prefix}_{ts}"

        # 4. 入库
        _own_client = client is None
        if client is None:
            from ai.config import get_ai_config
            client = self._make_qdrant_client(get_ai_config())

        try:
            result = await self.embed_and_upsert(chunks, collection_name, client=client)
        except Exception as e:
            logger.error(f"入库 embed/upsert 失败: {label}, error={e}", exc_info=True)
            self._log(f"[ERR] {label}: embed/upsert 失败 — {e}")
            if _own_client:
                try:
                    client.close()
                except Exception:
                    pass
            return False
        if result.get("status") != "ok":
            logger.error(f"入库结果异常: {label}, result={result}")
            self._log(f"[ERR] {label} 入库失败: {result}")
            if _own_client:
                try:
                    client.close()
                except Exception:
                    pass
            return False

        # 5. 更新指针
        old = self.pointer_reader()
        if old != collection_name:
            self.pointer_writer(collection_name)
            self._log(f"[SWITCH] {self.collection_prefix}: {old or '(new)'} -> {collection_name}")

        # 6. 清理旧集合
        await self.run_cleanup(keep=2, client=client)

        if _own_client:
            try:
                client.close()
            except Exception:
                pass

        self._log(f"[OK] {label} 入库完成: {collection_name}")
        return True

    async def run_cleanup(self, keep: int = 2, client=None) -> None:
        """删除旧集合，保留最新 keep 个。可复用外部 client。"""
        from ai.config import get_ai_config

        config = get_ai_config()
        active = self.pointer_reader()
        _own_client = client is None

        if client is None:
            client = self._make_qdrant_client(config)

        try:
            all_cols = [c.name for c in client.get_collections().collections]
        except Exception as e:
            logger.error(f"获取 Qdrant 集合列表失败: {e}", exc_info=True)
            self._log(f"[ERR] 获取集合列表失败: {e}")
            if _own_client:
                try:
                    client.close()
                except Exception:
                    pass
            return

        # 只匹配同前缀 + 时间戳后缀的集合：cheduan_YYYYMMDD 不等于 cheduan_manual_YYYYMMDD
        # 用正则确保 prefix_ 之后紧跟数字，避免 cheduan 误匹配 cheduan_manual
        prefix_pat = re.compile(rf"^{re.escape(self.collection_prefix)}_\d")
        ours = sorted(
            [c for c in all_cols if prefix_pat.match(c)],
            reverse=True,
        )
        self._log(f"[CLEANUP] {self.collection_prefix}: 找到 {len(ours)} 个集合，保留最新 {keep} 个")
        if active:
            self._log(f"   活跃: {active}")

        deleted = 0
        for c in ours:
            # 永远不删活跃集合
            if c == active:
                self._log(f"   [KEEP] {c} (活跃)")
                continue
            if c in ours[:keep]:
                self._log(f"   [KEEP] {c}")
                continue
            try:
                client.delete_collection(c)
                self._log(f"   [DEL] {c}")
                deleted += 1
            except Exception as e:
                self._log(f"   [ERR] 删除 {c} 失败: {e}")

        if deleted:
            self._log(f"[OK] 清理完成，删除 {deleted} 个旧集合")
        else:
            self._log(f"[OK] 无需清理")

        if _own_client:
            try:
                client.close()
            except Exception:
                pass

    def run_dry_run(self) -> None:
        """预览模式：解析并打印摘要，不写 Qdrant。"""
        label = self.get_source_label()

        if not self.validate_source_files():
            self._log(f"[SKIP] 源文件缺失，预览中止")
            return

        entries = self.parse()
        self._log(f"\n[Dry-run] {label}: {len(entries)} 条数据")

        if not entries:
            return

        # 显示前 5 条
        for i, e in enumerate(entries[:5]):
            chunk = self.to_chunk(e)
            preview = chunk.text[:100].replace('\n', ' / ')
            self._log(f"  [{i + 1}] {preview}...")

        if len(entries) > 5:
            self._log(f"  ... 共 {len(entries)} 条")

    # ================================================================
    # CLI 入口（子类通过 cls.run_cli() 调用）
    # ================================================================

    @classmethod
    async def run_cli(cls):
        """标准 CLI：--rebuild / --dry-run / --cleanup"""
        import argparse

        parser = argparse.ArgumentParser(
            description=f"{cls.__name__} → Qdrant 知识库导入",
        )
        parser.add_argument("--rebuild", "-r", action="store_true", help="入库")
        parser.add_argument("--dry-run", "-n", action="store_true", help="预览")
        parser.add_argument("--cleanup", action="store_true", help="清理旧集合")
        args = parser.parse_args()

        ingester = cls()

        if args.cleanup:
            await ingester.run_cleanup()
            return

        if args.dry_run:
            ingester.run_dry_run()
            return

        ok = await ingester.auto_ingest()
        if not ok:
            sys.exit(1)

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _make_qdrant_client(config):
        """创建 Qdrant 客户端（统一处理 local vs remote）"""
        from qdrant_client import QdrantClient

        if config.qdrant_local_path:
            local = Path(config.qdrant_local_path)
            if not local.is_absolute():
                local = _project_root / local
            return QdrantClient(path=str(local))
        else:
            return QdrantClient(
                host=config.qdrant_host,
                port=config.qdrant_port,
                timeout=config.qdrant_timeout,
                check_compatibility=False,
            )

    @staticmethod
    def stable_id(*parts: str) -> str:
        """生成稳定的 MD5 ID"""
        return hashlib.md5(":".join(parts).encode()).hexdigest()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)


# ================================================================
# 便捷工厂：从函数创建 Ingester
# ================================================================

class FunctionalIngester(BaseIngester[T]):
    """
    轻量级 Ingester——不需要定义子类，直接传入 parse / to_chunk 函数即可。

    适用于简单场景或不想为一次性格式单独建文件的临时入库。
    """
    def __init__(
        self,
        parse_fn: Callable[[], List[T]],
        to_chunk_fn: Callable[[T], Chunk],
        source_paths: List[Path],
        collection_prefix: str,
        pointer_reader: Callable[[], str],
        pointer_writer: Callable[[str], None],
        collection_type: str = "",
        rebuild: bool = True,
    ):
        self._parse_fn = parse_fn
        self._to_chunk_fn = to_chunk_fn
        self.source_paths = source_paths
        self.collection_prefix = collection_prefix
        self.pointer_reader = pointer_reader
        self.pointer_writer = pointer_writer
        self.collection_type = collection_type
        self.rebuild = rebuild

    def parse(self) -> List[T]:
        return self._parse_fn()

    def to_chunk(self, entry: T) -> Chunk:
        return self._to_chunk_fn(entry)
