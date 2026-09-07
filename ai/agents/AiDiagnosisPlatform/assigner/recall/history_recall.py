"""L3-A路 历史召回：Qdrant 语义检索相似历史工单 → 按 engineer_id 聚合

这是历史召回（L3）的 A路——「按相似工单聚人」：
- 从 Qdrant dispatch_history 检索与当前工单相似的已解决/已关闭工单
- 按解决人（engineer_id）聚合

数据源：Qdrant 独立集合 dispatch_history（见 ai/core/retrieval.py，
index_dispatch_history / retrieve_dispatch_history），由补索引脚本
（sync/history_indexer.py）写入 resolved+closed，每条 payload 带 engineer_id。

与之并行的问题域一路（见 recall/expertise_recall.py）按自动簇聚人。
两路独立进精排，不再合成一路；本路可空。

召回增强（相比纯余弦平均）：
1. 时间衰减：A/B 共用 time_decay，尽头 0.4（不会掉到 0）
2. 人分取该人各张旧单的最高分，截到 1.0；只有一张不打折
3. 故障码/车型强匹配：历史工单若与当前工单故障码/车型相同，直接 boost
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.recall.dispatch_text import (
    build_dispatch_ticket_text,
)
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


def similar_person_score(ticket_scores: List[float]) -> float:
    """相似工单人分：取各张最高，截到 1.0。只有一张就是那张，不打七折。"""
    if not ticket_scores:
        return 0.0
    return round(min(1.0, max(float(s) for s in ticket_scores)), 4)


# 兼容：旧实现用的缓存（Qdrant 化后不再用，保留惰性清理）
_cache = {
    "hist_recs": [],
    "hist_embs": [],
    "hist_hash": "",
}


def time_decay_factor(
    elapsed_days: float,
    half_life_days: float = 90.0,
    floor: float = 0.4,
) -> float:
    """A/B 共用时间衰减：刚结=1.0，无限久→floor，不会掉到 0。

    decay = floor + (1 - floor) * exp(-elapsed_days / half_life_days)
    """
    fl = min(max(float(floor), 0.0), 1.0)
    hl = max(float(half_life_days), 1e-6)
    days = max(float(elapsed_days), 0.0)
    return float(fl + (1.0 - fl) * np.exp(-days / hl))


def similar_person_score(ticket_scores: List[float]) -> float:
    """相似工单人分：取各张最高，截到 1.0。只有一张就是那张，不打七折。"""
    if not ticket_scores:
        return 0.0
    return round(min(1.0, max(float(s) for s in ticket_scores)), 4)


def time_decay(
    created_at,
    half_life_days: float = 90.0,
    floor: float = 0.4,
) -> float:
    """按解决/关闭时间做衰减。无时间信息时返回 1.0（不衰减）。"""
    if not created_at:
        return 1.0
    try:
        if isinstance(created_at, (int, float)):
            ts = float(created_at)
        elif isinstance(created_at, str):
            created_at = created_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(created_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        else:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            ts = created_at.timestamp()
        import time
        elapsed_days = max(0.0, (time.time() - ts) / 86400.0)
        return time_decay_factor(elapsed_days, half_life_days, floor)
    except Exception:
        return 1.0


class HistoryRecall:

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        # 召回增强参数（从 config.yaml 的 history_recall 读取）
        hc = self._config.history_recall or {}
        self._top_k = int(hc.get("top_k", 5))
        self._half_life_days = float(hc.get("half_life_days", 90))
        self._decay_floor = float(hc.get("decay_floor", 0.4))
        self._sim_threshold = float(hc.get("sim_threshold", 0.3))
        self._fault_boost = float(hc.get("fault_code_boost", 0.15))
        self._type_boost = float(hc.get("robot_type_boost", 0.10))
        self._retriever = None

    def _build_query_text(self, ticket: TicketContext) -> str:
        """工单文本 → Qdrant 检索查询（与入库四栏模板一致）"""
        return build_dispatch_ticket_text(
            ticket.title,
            ticket.problem_description,
            ticket.robot_type,
            ticket.fault_code,
        )

    async def _get_retriever(self):
        if self._retriever is None:
            from ai.core import get_retrieval_service
            self._retriever = await get_retrieval_service()
        return self._retriever

    async def arecall(self, ticket: TicketContext) -> Dict[str, float]:
        """A路：从 Qdrant 检索相似历史工单 → 按 engineer_id 聚合成分数。

        Returns:
            his: {engineer_id: score} — 历史工单匹配分数（0-1 未归一，供融合）
        """
        retriever = await self._get_retriever()
        q = self._build_query_text(ticket)
        if not q.strip():
            return {}

        # 当前工单的故障码/车型（用于强匹配）
        cur_fault = (ticket.fault_code or "").strip().lower()
        cur_robot = (ticket.robot_type or "").strip().lower()

        # 从 Qdrant 检索相似历史工单（返回带 engineer_id 的原始 points）
        hits = await retriever.retrieve_dispatch_history(q, top_k=30)
        if not hits:
            logger.debug(f"[派单:{ticket.id}] Step3-L3-A 相似工单: 无检索命中")
            return {}

        # 逐条算最终分：sim×融合 + 故障码/车型 boost，再按 engineer_id 聚合
        per_engineer: Dict[str, List[float]] = {}
        for h in hits:
            sim = float(h.get("score", 0.0))
            if sim < self._sim_threshold:
                continue
            eid = (h.get("engineer_id") or "").strip()
            if not eid:
                continue

            decay = time_decay(
                h.get("closed_at"), self._half_life_days, self._decay_floor,
            )
            rec_fault = (h.get("fault_code") or "").strip().lower()
            rec_robot = (h.get("robot_type") or "").strip().lower()
            boost = 0.0
            if cur_fault and cur_fault == rec_fault:
                boost += self._fault_boost
            if cur_robot and cur_robot == rec_robot:
                boost += self._type_boost

            final = sim * decay + boost
            per_engineer.setdefault(eid, []).append(final)

        his: Dict[str, float] = {}
        for eid, finals in per_engineer.items():
            his[eid] = similar_person_score(finals)

        logger.debug(
            f"[派单:{ticket.id}] Step3-L3-A 相似工单: 检索{len(hits)}条(过阈值{sum(1 for h in hits if float(h.get('score',0)) >= self._sim_threshold)}) "
            f"聚人={len(his)}人"
        )
        return his

    @staticmethod
    def empty_transfer_signals() -> Dict:
        """L3 转派旁路：本版恒返回空，不改 reassign / 不写 Qdrant。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import empty_transfer_signals
        return empty_transfer_signals()


def invalidate_history_cache():
    """清理兼容性缓存（Qdrant 化后无本地全量缓存，保留以防旧引用）"""
    global _cache
    _cache["hist_recs"] = []
    _cache["hist_embs"] = []
    _cache["hist_hash"] = ""
