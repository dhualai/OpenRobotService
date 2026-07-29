"""语义召回层：基于 Embedding 向量相似度的召回"""

import math
from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.history_sync import load_history_records
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cos(u, v):
    a = np.asarray(u); b = np.asarray(v)
    dot = np.dot(a, b); na = np.linalg.norm(a); nb = np.linalg.norm(b)
    return float(dot / (na * nb)) if na > 0 and nb > 0 else 0.0


class SemanticRecallResult:
    def __init__(self):
        self.engineer_semantic: Dict[str, float] = {}
        self.history_semantic: Dict[str, float] = {}


class SemanticRecaller:
    def __init__(self, config=None):
        self._config = config or AssignerConfig()
        self._eng_embs: Dict[str, List[float]] = {}
        self._hist_recs: List[dict] = []
        self._hist_embs: List[List[float]] = []
        self._pre = False

    def _build_eng_texts(self, engineers):
        out = []
        for e in engineers:
            t = f"责任模块: {', '.join(e.all_modules())}。"
            if e.duty_text:
                t += f"职责: {e.duty_text}"
            out.append(t)
        return out

    async def aprecompute(self, engineers):
        if self._pre:
            return
        from ai.core import get_embed_client
        ec = await get_embed_client()
        texts = self._build_eng_texts(engineers)
        if texts:
            embs = await ec.embed_batch(texts)
            for eng, emb in zip(engineers, embs):
                self._eng_embs[eng.id] = emb.tolist() if isinstance(emb, np.ndarray) else emb
        self._hist_recs = load_history_records(self._config.module_keywords)
        if self._hist_recs:
            htexts = [" ".join(filter(None, [r.get("title", ""), r.get("description", "")]))
                      for r in self._hist_recs]
            self._hist_embs = [emb.tolist() if isinstance(emb, np.ndarray) else emb
                               for emb in await ec.embed_batch(htexts)]
        self._pre = True

    async def arecall(self, ticket, engineers):
        r = SemanticRecallResult()
        if not self._pre:
            await self.aprecompute(engineers)
        from ai.core import get_embed_client
        ec = await get_embed_client()
        q = " ".join(filter(None, [ticket.title, ticket.problem_description, ticket.robot_type]))
        qe = (await ec.embed(q)).tolist()
        for eng in engineers:
            emb = self._eng_embs.get(eng.id)
            if emb:
                s = _cos(qe, emb)
                if s > 0:
                    r.engineer_semantic[eng.id] = s
        if self._hist_recs and self._hist_embs:
            hits: Dict[str, list] = {}
            for rec, he in zip(self._hist_recs, self._hist_embs):
                s = _cos(qe, he)
                if s > 0.3:
                    eid = rec.get("engineer_id", "").strip()
                    if eid:
                        hits.setdefault(eid, []).append(s)
            for eid, sims in hits.items():
                r.history_semantic[eid] = sum(sims) / len(sims)
        return r

    def reload(self):
        self._eng_embs.clear(); self._hist_recs.clear(); self._hist_embs.clear()
        self._pre = False
