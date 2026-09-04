"""CodeSkill 语义检索 — 基于 embedding 的离线代码语义召回。

背景（对比关键词检索）：
  关键词检索（retriever.py）对中文整句/口语化 query 召回很弱——「上轨」这类
  领域词若不在 query 前缀，或语义近似（如「入轨/汇入/重新上轨」）时，substring
  匹配常常落空，导致「代码检索没找到相关实现」。

本模块用 embedding（本地 bge-small-zh-v1.5，见 ai/core/embed.py）把每个函数的
「签名 + docstring」向量化，检索时对 query 也向量化后算余弦相似度，实现**语义召回**。
它完全离线：向量与对齐 id 存本地文件，不依赖在线 Qdrant，契合「服务器不放代码、
本地使用」的定位（见 TASK_AGENT_TARGET_ARCH §6b.3b）。

数据文件（与 code_index.json 同目录）：
  - code_index_semantic.npy          N×dim 的归一化向量矩阵
  - code_index_semantic_ids.json     与向量行对齐的函数指纹列表
      每项: {"name","file_path","line_start"}（与 FunctionRef 对齐用）

用法：
  idx = SemanticCodeIndex.load(base_dir)          # 懒加载向量（无则返回空）
  hits = await idx.search("上轨的逻辑", top_k=5)  # [(score, fingerprint), ...]
  await SemanticCodeIndex.build(functions, base_dir)  # 离线建索引
"""

import asyncio
import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


def _fingerprint(f) -> dict:
    """函数 → 可 hash/可对齐的指纹 dict（与 code_index.json 的字段一致）。"""
    return {
        "name": f.get("name") or "",
        "file_path": f.get("file_path") or "",
        "line_start": int(f.get("line_start") or 0),
    }


def _doc_text(f) -> str:
    """构造函数可向量化文本：签名 + docstring（docstring 决定语义质量）。"""
    sig = (f.get("signature") or "").strip()
    doc = (f.get("docstring") or "").strip()
    return f"{sig}. {doc}" if doc else sig


class SemanticCodeIndex:
    """本地 embedding 语义索引（离线、无 Qdrant 依赖）。"""

    NPY_NAME = "code_index_semantic.npy"
    IDS_NAME = "code_index_semantic_ids.json"

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._mat: Optional[np.ndarray] = None
        self._ids: List[dict] = []
        self._id_to_pos: Dict[str, int] = {}
        self._loaded = False

    # ------------------------------------------------------------
    # 构建（离线一次）
    # ------------------------------------------------------------
    @classmethod
    async def build(
        cls,
        functions: List[dict],
        base_dir: str,
    ) -> "SemanticCodeIndex":
        """给一批函数构建语义向量，写入 base_dir。

        functions: code_index.json 的 functions 列表（含 name/file_path/line_start/
                   signature/docstring）。docstring 缺失时尽量用签名兜底。
        失败（模型不可用）时返回空索引并记录 warning，不影响主流程。
        """
        idx = cls(base_dir)
        if not functions:
            logger.warning("[semantic] 无函数可建语义索引")
            return idx

        try:
            from ai.core.embed import get_embed_client
            embed = await get_embed_client()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[semantic] embedding 客户端不可用，跳过语义索引: {e}")
            return idx

        texts = [_doc_text(f) for f in functions]
        logger.info(f"[semantic] 向量化 {len(texts)} 个函数...")
        try:
            vecs = await embed.embed_batch(texts, normalize=True, batch_size=64)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[semantic] 向量化失败，跳过语义索引: {e}")
            return idx

        os.makedirs(base_dir, exist_ok=True)
        mat = np.vstack(vecs).astype(np.float32)
        ids = [_fingerprint(f) for f in functions]
        np.save(os.path.join(base_dir, cls.NPY_NAME), mat)
        with open(os.path.join(base_dir, cls.IDS_NAME), "w", encoding="utf-8") as fh:
            json.dump(ids, fh, ensure_ascii=False)

        idx._mat = mat
        idx._ids = ids
        idx._rebuild_index()
        idx._loaded = True
        logger.info(f"[semantic] 语义索引已生成: {len(ids)} 个函数, dim={mat.shape[1]}")
        return idx

    # ------------------------------------------------------------
    # 加载（懒加载）
    # ------------------------------------------------------------
    @classmethod
    def load(cls, base_dir: str) -> "SemanticCodeIndex":
        """从 base_dir 懒加载向量与对齐 id；文件缺失则返回空索引（语义召回为空）。"""
        idx = cls(base_dir)
        npy = os.path.join(base_dir, cls.NPY_NAME)
        ids = os.path.join(base_dir, cls.IDS_NAME)
        if not (os.path.exists(npy) and os.path.exists(ids)):
            logger.info(f"[semantic] 语义索引文件缺失（{cls.NPY_NAME}），将回退关键词检索")
            return idx
        try:
            idx._mat = np.load(npy)
            with open(ids, encoding="utf-8") as fh:
                idx._ids = json.load(fh)
            idx._rebuild_index()
            idx._loaded = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[semantic] 语义索引加载失败，回退关键词检索: {e}")
            idx._mat = None
            idx._ids = []
            idx._id_to_pos = {}
            idx._loaded = False
        return idx

    def _rebuild_index(self) -> None:
        self._id_to_pos = {
            _fp_key(i): pos for pos, i in enumerate(self._ids)
        }

    @property
    def is_ready(self) -> bool:
        return self._loaded and self._mat is not None and len(self._ids) > 0

    def __len__(self) -> int:
        return len(self._ids) if self.is_ready else 0

    # ------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------
    async def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.35,
    ) -> List[Tuple[float, dict]]:
        """query → embedding → 余弦相似度召回 Top K。

        返回 [(score, fingerprint), ...]（按相似度降序）。
        score_threshold: 低于该相似度的结果丢弃，避免无关噪音。
        """
        if not self.is_ready or not query:
            return []
        try:
            from ai.core.embed import get_embed_client
            embed = await get_embed_client()
            qv = await embed.embed(query, normalize=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[semantic] query 向量化失败: {e}")
            return []

        sims = self._mat @ qv
        order = np.argsort(-sims)
        hits: List[Tuple[float, dict]] = []
        for pos in order:
            score = float(sims[pos])
            if score < score_threshold:
                break  # 已降序，后续更低
            hits.append((score, self._ids[int(pos)]))
            if len(hits) >= top_k:
                break
        return hits

    def position_of(self, fp: dict) -> Optional[int]:
        """函数指纹 → 向量行位置（供融合时按函数对齐）。"""
        return self._id_to_pos.get(_fp_key(fp))


def _fp_key(fp: dict) -> str:
    """指纹 → 去重 key（name|file_path|line_start）。"""
    return f"{fp.get('name')}|{fp.get('file_path')}|{fp.get('line_start')}"
