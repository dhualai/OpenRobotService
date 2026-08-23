"""L3-A路 历史召回：Qdrant 语义检索相似历史工单 → 按 engineer_id 聚合

这是历史召回（L3）的 A路——「按相似工单聚人」：
- 从 Qdrant dispatch_history 集合语义检索与当前工单相似的已关闭工单
- 按解决人（engineer_id）聚合

数据源：Qdrant 独立集合 dispatch_history（见 ai/core/retrieval.py，
index_dispatch_history / retrieve_dispatch_history），由补索引脚本
（sync/history_indexer.py）与工单闭环持续写入，每条 payload 带 engineer_id。

与之并行的 B路（见 recall/expertise_recall.py）按「问题域聚人」。
两者在 dispatch_flow._merge_history 中融合。

召回增强（相比纯余弦平均）：
1. 时间衰减：越近关闭的历史工单权重越高（指数衰减，半衰期默认 90 天）
2. Top-K 聚合：取每人最高的 K 条，按 Top1 覆盖加权，避免模糊历史摊平关键经验
3. 故障码/车型强匹配：历史工单若与当前工单故障码/车型相同，直接 boost
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


# 兼容：旧实现用的缓存（Qdrant 化后不再用，保留惰性清理）
_cache = {
    "hist_recs": [],
    "hist_embs": [],
    "hist_hash": "",
}


def _time_decay(created_at, half_life_days: float) -> float:
    """按解决时间做指数衰减：时间越近权重越高。

    decay = exp(-elapsed_days / half_life_days)
    无时间信息时返回 1.0（不衰减）。
    """
    if not created_at:
        return 1.0
    try:
        if isinstance(created_at, (int, float)):
            ts = created_at
        else:
            # datetime 或含 T 的字符串
            if isinstance(created_at, str):
                created_at = created_at.replace("Z", "+00:00")
                dt = datetime.fromisoformat(created_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.timestamp()
            else:
                # naive datetime
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                ts = created_at.timestamp()
        import time
        elapsed_days = max(0.0, (time.time() - ts) / 86400.0)
        return float(np.exp(-elapsed_days / half_life_days))
    except Exception:
        return 1.0


class HistoryRecall:

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        # 召回增强参数（从 config.yaml 的 history_recall 读取）
        hc = self._config.history_recall or {}
        self._top_k = int(hc.get("top_k", 5))
        self._half_life_days = float(hc.get("half_life_days", 90))
        self._sim_threshold = float(hc.get("sim_threshold", 0.3))
        self._fault_boost = float(hc.get("fault_code_boost", 0.15))
        self._type_boost = float(hc.get("robot_type_boost", 0.10))
        self._decay_weight = float(hc.get("decay_weight", 0.5))
        self._retriever = None

    def _build_query_text(self, ticket: TicketContext) -> str:
        """工单文本 → Qdrant 检索查询（与入库 index_text 语义一致）"""
        return " ".join(filter(None, [
            ticket.title or "",
            ticket.problem_description or "",
            ticket.robot_type or "",
            ticket.fault_code or "",
        ]))

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

            # 时间衰减（用 closed_at；无则 1.0）
            decay = _time_decay(h.get("closed_at"), self._half_life_days)
            # 故障码/车型强匹配 boost
            rec_fault = (h.get("fault_code") or "").strip().lower()
            rec_robot = (h.get("robot_type") or "").strip().lower()
            boost = 0.0
            if cur_fault and cur_fault == rec_fault:
                boost += self._fault_boost
            if cur_robot and cur_robot == rec_robot:
                boost += self._type_boost

            # 时间衰减与相似度融合 + boost
            final = sim * (self._decay_weight + (1.0 - self._decay_weight) * decay) + boost
            per_engineer.setdefault(eid, []).append(final)

        # Top-K 聚合：取每人最高的 K 条，按 Top1 覆盖加权，避免模糊历史摊平
        his: Dict[str, float] = {}
        for eid, finals in per_engineer.items():
            finals.sort(reverse=True)
            top_k = finals[: self._top_k]
            top1 = top_k[0]
            avg_rest = sum(top_k[1:]) / len(top_k[1:]) if len(top_k) > 1 else 0.0
            # 峰值加权：Top1 占主导，其余仅轻微补充
            his[eid] = round(0.7 * top1 + 0.3 * avg_rest, 4)

        logger.debug(
            f"[派单:{ticket.id}] Step3-L3-A 相似工单: 检索{len(hits)}条(过阈值{sum(1 for h in hits if float(h.get('score',0)) >= self._sim_threshold)}) "
            f"聚人={len(his)}人"
        )
        return his


def invalidate_history_cache():
    """清理兼容性缓存（Qdrant 化后无本地全量缓存，保留以防旧引用）"""
    global _cache
    _cache["hist_recs"] = []
    _cache["hist_embs"] = []
    _cache["hist_hash"] = ""
