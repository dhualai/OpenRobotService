"""L2/L3 模块+历史召回

- L2 semantic_score: cos(工单, 模块锚文本) → 按工程师 responsibility_modules 反查
- L3 history_score:  cos(工单, 历史已解决工单) → 按 engineer_id 聚合

模块锚文本配置在 assigner_config.yaml 的 module_anchor_texts 字段。
"""

from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import load_history_records
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cos(u, v):
    a = np.asarray(u); b = np.asarray(v)
    dot = np.dot(a, b); na = np.linalg.norm(a); nb = np.linalg.norm(b)
    return float(dot / (na * nb)) if na > 0 and nb > 0 else 0.0


# ── 模块级缓存 ──
_cache = {
    "module_embeddings": {},    # {模块名: embedding}
    "anchor_hash": "",          # 锚文本变更检测
    "hist_recs": [],
    "hist_embs": [],
    "hist_hash": "",
}


class SemanticRecaller:

    def __init__(self, config=None):
        self._config = config or AssignerConfig()

    def _build_module_tickets(
        self, ticket: TicketContext
    ) -> str:
        """工单文本 → Embedding 查询用"""
        return " ".join(filter(None, [
            ticket.title or "",
            ticket.problem_description or "",
            ticket.robot_type or "",
            ticket.fault_code or "",
        ]))

    async def _ensure_module_cache(self):
        """预计算模块锚文本 embedding（锚文本变更时重算）"""
        global _cache
        anchors = self._config.module_anchor_texts or {}
        if not anchors:
            return

        import hashlib, json
        h = hashlib.md5(json.dumps(anchors, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if _cache["anchor_hash"] == h and _cache["module_embeddings"]:
            return

        from ai.core import get_embed_client
        ec = await get_embed_client()

        mod_names = list(anchors.keys())
        mod_texts = [anchors[n] for n in mod_names]
        embs = await ec.embed_batch(mod_texts)
        _cache["module_embeddings"] = {}
        for name, emb in zip(mod_names, embs):
            _cache["module_embeddings"][name] = emb.tolist() if isinstance(emb, np.ndarray) else emb
        _cache["anchor_hash"] = h

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

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> tuple[Dict[str, float], Dict[str, float]]:
        """
        Returns:
            sem: {engineer_id: score} — 模块匹配分数
            his: {engineer_id: score} — 历史工单匹配分数
        """
        await self._ensure_module_cache()
        await self._ensure_history_cache()

        from ai.core import get_embed_client
        ec = await get_embed_client()
        q = self._build_module_tickets(ticket)
        qe = (await ec.embed(q)).tolist()

        # ── L2 模块召回 ──
        sem: Dict[str, float] = {}
        module_embs = _cache["module_embeddings"]
        if module_embs:
            # 工单 vs 每个模块锚文本 → 取匹配分数
            module_scores: Dict[str, float] = {}
            for mod_name, memb in module_embs.items():
                s = _cos(qe, memb)
                if s > 0.3:
                    module_scores[mod_name] = s

            # 按模块分数反查工程师 → 加权累计
            for eng in engineers:
                score = 0.0
                for prod, mods in eng.responsibility_modules.items():
                    for mod in mods:
                        if mod in module_scores:
                            score = max(score, module_scores[mod])
                if score > 0:
                    sem[eng.id] = score

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

        return sem, his


def invalidate_semantic_cache():
    global _cache
    _cache["module_embeddings"] = {}
    _cache["anchor_hash"] = ""
    _cache["hist_recs"] = []
    _cache["hist_embs"] = []
    _cache["hist_hash"] = ""
