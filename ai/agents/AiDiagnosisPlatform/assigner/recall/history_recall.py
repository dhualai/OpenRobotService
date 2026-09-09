"""Step3·相似工单：Qdrant 检索近邻历史单 → 按处理人聚合。

看的是「像这张单的旧单上当时谁结的」。一张极像的旧单就能把人捞上来。
数据源：Qdrant dispatch_history；结单增量写入，开发者模式可全量补索引。

与 [问题簇] 并行：那边看的是一类问题堆里的常客。两路独立进精排，本路可空。
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.recall.dispatch_text import (
    build_dispatch_ticket_text,
)
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


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


MISASSIGN_FEED = "reassign"


def score_similar_hits(
    hits: List[dict],
    *,
    sim_threshold: float,
    half_life_days: float,
    decay_floor: float,
    cur_fault: str = "",
    cur_robot: str = "",
    fault_boost: float = 0.15,
    type_boost: float = 0.10,
    confirm_boost: float = 0.15,
    reject_factor: float = 0.70,
    extra_pairs: Optional[List[dict]] = None,
) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, str]]:
    """把检索命中聚成人分。纠错样本加减分；相似单上发生过派错了也压原处理人。

    extra_pairs: [{from_id, to_id, reason}]，由相似命中的 ticket_id 反查操作日志。
    """
    per_engineer: Dict[str, List[float]] = {}
    confirmed: Dict[str, str] = {}
    rejected: Dict[str, str] = {}
    cur_fault = (cur_fault or "").strip().lower()
    cur_robot = (cur_robot or "").strip().lower()

    for h in hits:
        sim = float(h.get("score", 0.0))
        if sim < sim_threshold:
            continue
        eid = (h.get("engineer_id") or "").strip()
        if not eid:
            continue

        decay = time_decay(
            h.get("closed_at"), half_life_days, decay_floor,
        )
        rec_fault = (h.get("fault_code") or "").strip().lower()
        rec_robot = (h.get("robot_type") or "").strip().lower()
        boost = 0.0
        if cur_fault and cur_fault == rec_fault:
            boost += fault_boost
        if cur_robot and cur_robot == rec_robot:
            boost += type_boost

        final = sim * decay + boost
        feed = (h.get("feed_type") or "normal").strip()
        reason = (h.get("reason") or "").strip()
        if feed == MISASSIGN_FEED:
            final = min(1.0, final + max(float(confirm_boost), 0.0))
            confirmed[eid] = reason or confirmed.get(eid, "")
            rid = (h.get("rejected_id") or "").strip()
            if rid and rid != eid:
                rejected[rid] = reason or rejected.get(rid, "")

        per_engineer.setdefault(eid, []).append(final)

    for pair in extra_pairs or []:
        a = str((pair or {}).get("from_id") or "").strip()
        b = str((pair or {}).get("to_id") or "").strip()
        reason = str((pair or {}).get("reason") or "").strip()
        if b:
            confirmed[b] = reason or confirmed.get(b, "")
        if a and a != b:
            rejected[a] = reason or rejected.get(a, "")

    his: Dict[str, float] = {}
    for eid, finals in per_engineer.items():
        his[eid] = similar_person_score(finals)

    factor = min(max(float(reject_factor), 0.0), 1.0)
    for rid in rejected:
        if rid in his:
            his[rid] = round(his[rid] * factor, 4)

    return his, confirmed, rejected


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
        # retrieve_top_k：Qdrant 检索条数。缺省 30；不要回落到旧键 top_k=5，会把召回收窄。
        self._retrieve_top_k = max(1, int(hc.get("retrieve_top_k", 30)))
        self._half_life_days = float(hc.get("half_life_days", 90))
        self._decay_floor = float(hc.get("decay_floor", 0.4))
        self._sim_threshold = float(hc.get("sim_threshold", 0.3))
        self._fault_boost = float(hc.get("fault_code_boost", 0.15))
        self._type_boost = float(hc.get("robot_type_boost", 0.10))
        self._confirm_boost = float(hc.get("misassign_confirm_boost", 0.15))
        self._reject_factor = float(hc.get("misassign_reject_factor", 0.70))
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

    async def arecall(
        self,
        ticket: TicketContext,
        feedback: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Dict[str, float]:
        """A路：从 Qdrant 检索相似历史工单 → 按 engineer_id 聚合成分数。

        feedback 若传入，写入 confirmed / rejected（派错纠正样本）。
        """
        retriever = await self._get_retriever()
        q = self._build_query_text(ticket)
        if not q.strip():
            return {}

        hits = await retriever.retrieve_dispatch_history(q, top_k=self._retrieve_top_k)
        if not hits:
            logger.debug(f"[派单:{ticket.id}] Step3 相似工单: 无检索命中")
            return {}

        extra_pairs = []
        try:
            from ai.agents.AiDiagnosisPlatform.assigner.sync.reassign_stats import (
                load_correction_pairs,
            )
            tids = [h.get("ticket_id") for h in hits if h.get("ticket_id")]
            extra_pairs = load_correction_pairs(tids) or []
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] Step3 相似工单 纠错对反查失败: {e}")

        his, confirmed, rejected = score_similar_hits(
            hits,
            sim_threshold=self._sim_threshold,
            half_life_days=self._half_life_days,
            decay_floor=self._decay_floor,
            cur_fault=ticket.fault_code or "",
            cur_robot=ticket.robot_type or "",
            fault_boost=self._fault_boost,
            type_boost=self._type_boost,
            confirm_boost=self._confirm_boost,
            reject_factor=self._reject_factor,
            extra_pairs=extra_pairs,
        )
        if feedback is not None:
            feedback["confirmed"] = confirmed
            feedback["rejected"] = rejected
        extra = ""
        if confirmed or rejected:
            extra = f" 纠正+{len(confirmed)} 错派-{len(rejected)}"
        logger.debug(
            f"[派单:{ticket.id}] Step3 相似工单: 检索{len(hits)}条"
            f"(过阈值{sum(1 for h in hits if float(h.get('score',0)) >= self._sim_threshold)}) "
            f"聚人={len(his)}人{extra}"
        )
        return his
