"""L3 语义召回：Embedding 向量相似度"""

from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cos(u, v):
    a = np.asarray(u); b = np.asarray(v)
    dot = np.dot(a, b); na = np.linalg.norm(a); nb = np.linalg.norm(b)
    return float(dot / (na * nb)) if na > 0 and nb > 0 else 0.0


class SemanticRecaller:
    def __init__(self, config=None):
        self._config = config or AssignerConfig()
        self._eng_embs: Dict[str, List[float]] = {}
        self._pre = False

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
        self._pre = True

    async def arecall(self, ticket, engineers) -> Dict[str, float]:
        if not self._pre:
            await self.aprecompute(engineers)
        from ai.core import get_embed_client
        ec = await get_embed_client()
        q = " ".join(filter(None, [ticket.title, ticket.problem_description, ticket.robot_type]))
        qe = (await ec.embed(q)).tolist()
        scores = {}
        for eng in engineers:
            emb = self._eng_embs.get(eng.id)
            if emb:
                s = _cos(qe, emb)
                if s > 0:
                    scores[eng.id] = s
        return scores

    def reload(self):
        self._eng_embs.clear(); self._pre = False
