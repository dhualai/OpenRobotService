"""L3/L4 语义+历史召回：共享 Embedding，一次查询返回两路分数

- semantic_score: cos(工单嵌入, 工程师画像嵌入)
- history_score: cos(工单嵌入, 历史已解决工单嵌入) → 按 engineer_id 聚合

模块级单例 _cache — Embedding 在首次请求时计算，后续复用。
人员变更时调 invalidate() 或等 engineers 列表变化自动刷新。
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


def _eng_fingerprint(engineers: List[EngineerProfile]) -> str:
    """用人员 ID 排序拼接做缓存指纹，人员变更时自动失效。"""
    ids = sorted(e.id for e in engineers)
    return ",".join(ids)


# ── 模块级单例缓存 ──
_cache = {
    "hash": "",                  # 人员指纹
    "eng_embs": {},
    "hist_recs": [],
    "hist_embs": [],
}


class SemanticRecaller:

    def __init__(self, config=None):
        self._config = config or AssignerConfig()

    def _build_eng_texts(self, engineers):
        out = []
        for e in engineers:
            parts = []
            for prod, mods in e.responsibility_modules.items():
                if mods:
                    parts.append(f"[{prod}] {', '.join(mods)}")
                else:
                    parts.append(f"[{prod}]")
            t = " | ".join(parts) if parts else "无责任模块"
            if e.duty_text:
                t += f" [职责] {e.duty_text}"
            out.append(t)
        return out

    async def _ensure_precomputed(self, engineers):
        global _cache
        h = _eng_texts_hash(engineers)
        if _cache["hash"] == h and _cache["eng_embs"]:
            return  # 缓存命中

        from ai.core import get_embed_client
        ec = await get_embed_client()

        texts = self._build_eng_texts(engineers)
        if texts:
            embs = await ec.embed_batch(texts)
            _cache["eng_embs"] = {}
            for eng, emb in zip(engineers, embs):
                _cache["eng_embs"][eng.id] = emb.tolist() if isinstance(emb, np.ndarray) else emb

        _cache["hist_recs"] = load_history_records(self._config.module_keywords)
        if _cache["hist_recs"]:
            htexts = [" ".join(filter(None, [r.get("title", ""), r.get("description", "")]))
                      for r in _cache["hist_recs"]]
            _cache["hist_embs"] = [emb.tolist() if isinstance(emb, np.ndarray) else emb
                                   for emb in await ec.embed_batch(htexts)]
        else:
            _cache["hist_embs"] = []

        _cache["hash"] = h

    async def arecall(self, ticket, engineers
                      ) -> tuple[Dict[str, float], Dict[str, float]]:
        await self._ensure_precomputed(engineers)

        from ai.core import get_embed_client
        ec = await get_embed_client()
        q = " ".join(filter(None, [ticket.title, ticket.problem_description, ticket.robot_type]))
        qe = (await ec.embed(q)).tolist()

        # 画像语义
        sem = {}
        for eng in engineers:
            emb = _cache["eng_embs"].get(eng.id)
            if emb:
                s = _cos(qe, emb)
                if s > 0:
                    sem[eng.id] = s

        # 历史工单语义 → 按 engineer_id 聚合
        his = {}
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
    _cache["hash"] = ""
    _cache["eng_embs"] = {}
    _cache["hist_recs"] = []
    _cache["hist_embs"] = []
