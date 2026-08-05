"""L3 历史召回：cos(工单, 历史已解决工单) → 按 engineer_id 聚合

历史工单来自 sync/history_sync.py 拉取的已解决任务记录。
"""

from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import load_history_records
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext


def _cos(u, v):
    a = np.asarray(u); b = np.asarray(v)
    dot = np.dot(a, b); na = np.linalg.norm(a); nb = np.linalg.norm(b)
    return float(dot / (na * nb)) if na > 0 and nb > 0 else 0.0


# ── 历史工单缓存 ──
_cache = {
    "hist_recs": [],
    "hist_embs": [],
    "hist_hash": "",
}


class HistoryRecall:

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    def _build_query_text(self, ticket: TicketContext) -> str:
        """工单文本 → Embedding 查询用"""
        return " ".join(filter(None, [
            ticket.title or "",
            ticket.problem_description or "",
            ticket.robot_type or "",
            ticket.fault_code or "",
        ]))

    async def _ensure_history_cache(self):
        """预计算历史工单 embedding"""
        global _cache
        recs = load_history_records(self._config.module_keywords)
        if not recs:
            return

        import hashlib, json
        h = hashlib.md5(json.dumps(recs, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        if _cache["hist_hash"] == h and _cache["hist_embs"]:
            return

        from ai.core import get_embed_client
        ec = await get_embed_client()
        htexts = [" ".join(filter(None, [r.get("title", ""), r.get("description", "")]))
                  for r in recs]
        _cache["hist_recs"] = recs
        _cache["hist_embs"] = [emb.tolist() if isinstance(emb, np.ndarray) else emb
                               for emb in await ec.embed_batch(htexts)]
        _cache["hist_hash"] = h

    async def arecall(self, ticket: TicketContext) -> Dict[str, float]:
        """
        Returns:
            his: {engineer_id: score} — 历史工单匹配分数
        """
        await self._ensure_history_cache()

        from ai.core import get_embed_client
        ec = await get_embed_client()
        q = self._build_query_text(ticket)
        qe = (await ec.embed(q)).tolist()

        # ── L3 历史召回 ──
        his: Dict[str, float] = {}
        his_recs = _cache["hist_recs"]
        his_embs = _cache["hist_embs"]
        if his_recs and his_embs:
            hits: Dict[str, list] = {}
            for rec, he in zip(his_recs, his_embs):
                s = _cos(qe, he)
                if s > 0.3:
                    eid = rec.get("engineer_id", "").strip()
                    if eid:
                        hits.setdefault(eid, []).append(s)
            for eid, sims in hits.items():
                his[eid] = sum(sims) / len(sims)

        return his


def invalidate_history_cache():
    global _cache
    _cache["hist_recs"] = []
    _cache["hist_embs"] = []
    _cache["hist_hash"] = ""
