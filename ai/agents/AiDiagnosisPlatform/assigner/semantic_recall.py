"""语义召回层：基于 Embedding 向量相似度的召回

召回维度：
1. 工程师画像语义召回 — 工单描述 vs 工程师画像文本
2. 历史任务语义召回 — 工单描述 vs 历史任务 description

预计算：启动时一次性预计算所有静态数据 Embedding，运行时只计算工单描述。
"""

import asyncio
import math
from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.history_sync import load_history_records
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cosine_similarity(vec_a, vec_b) -> float:
    """计算两个向量（np.ndarray 或 list）的余弦相似度（-1 ~ 1）。"""
    a = np.asarray(vec_a)
    b = np.asarray(vec_b)
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class SemanticRecallResult:
    """语义召回结果"""

    def __init__(self):
        self.engineer_semantic: Dict[str, float] = {}
        self.history_semantic: Dict[str, float] = {}


class SemanticRecaller:
    """基于 Embedding 的语义召回器"""

    def __init__(
        self,
        config: Optional[AssignerConfig] = None,
    ):
        self._config = config or AssignerConfig()
        self._engineer_embeddings: Dict[str, List[float]] = {}
        self._history_records: List[dict] = []
        self._history_embeddings: List[List[float]] = []
        self._precomputed = False

    def _build_engineer_texts(self, engineers: List[EngineerProfile]) -> List[str]:
        """构造工程师画像文本列表。"""
        eng_texts = []
        for eng in engineers:
            text = f"责任模块: {', '.join(eng.responsibility_modules)}。"
            if eng.duty_text:
                text += f"职责: {eng.duty_text}"
            eng_texts.append(text)
        return eng_texts

    async def aprecompute(self, engineers: List[EngineerProfile]) -> None:
        """异步预计算工程师画像和历史任务的 Embedding。"""
        if self._precomputed:
            return

        from ai.core import get_embed_client
        embed_client = await get_embed_client()

        eng_texts = self._build_engineer_texts(engineers)
        if eng_texts:
            embeddings = await embed_client.embed_batch(eng_texts)
            for eng, emb in zip(engineers, embeddings):
                self._engineer_embeddings[eng.id] = emb.tolist() if isinstance(emb, np.ndarray) else emb

        self._history_records = load_history_records(self._config.module_keywords)
        if self._history_records:
            hist_texts = [
                " ".join(filter(None, [rec.get("title", ""), rec.get("description", "")]))
                for rec in self._history_records
            ]
            self._history_embeddings = [
                emb.tolist() if isinstance(emb, np.ndarray) else emb
                for emb in await embed_client.embed_batch(hist_texts)
            ]

        self._precomputed = True

    async def arecall(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> SemanticRecallResult:
        """异步执行语义召回。"""
        result = SemanticRecallResult()

        if not self._precomputed:
            await self.aprecompute(engineers)

        from ai.core import get_embed_client
        embed_client = await get_embed_client()

        query_text = " ".join(filter(None, [ticket.title, ticket.problem_description, ticket.robot_type]))
        query_emb = (await embed_client.embed(query_text)).tolist()

        for eng in engineers:
            eng_emb = self._engineer_embeddings.get(eng.id)
            if eng_emb:
                sim = _cosine_similarity(query_emb, eng_emb)
                if sim > 0:
                    result.engineer_semantic[eng.id] = sim

        if self._history_records and self._history_embeddings:
            engineer_hits: Dict[str, List[float]] = {}
            for rec, hist_emb in zip(self._history_records, self._history_embeddings):
                sim = _cosine_similarity(query_emb, hist_emb)
                if sim > 0.3:
                    eid = rec.get("engineer_id", "").strip()
                    if eid:
                        engineer_hits.setdefault(eid, []).append(sim)

            for eid, sims in engineer_hits.items():
                result.history_semantic[eid] = sum(sims) / len(sims)

        return result

    def reload(self) -> None:
        """重新预计算（数据更新后调用）。"""
        self._engineer_embeddings.clear()
        self._history_records.clear()
        self._history_embeddings.clear()
        self._precomputed = False
