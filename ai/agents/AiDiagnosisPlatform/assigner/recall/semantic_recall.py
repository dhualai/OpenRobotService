"""L2 语义召回：cos(工单, 模块锚文本) → 按工程师 responsibility_modules 反查

模块锚文本配置在 config.yaml 的 module_anchor_texts 字段（key 为「产品-类别」组合，
以便区分不同产品下同名模块——如调度USP-算法 与 摇人吧服务号-算法 负责的人不同）。
工程师责任模块的细分名通过 config.yaml 的 module_classify（产品→{细分模块: 类别}）
映射到类别，再组装成「产品-类别」key 匹配模块锚文本。
"""

from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cos(u, v):
    a = np.asarray(u); b = np.asarray(v)
    dot = np.dot(a, b); na = np.linalg.norm(a); nb = np.linalg.norm(b)
    return float(dot / (na * nb)) if na > 0 and nb > 0 else 0.0


# ── 模块级缓存 ──
_cache = {
    "module_embeddings": {},    # {模块名: embedding}
    "anchor_hash": "",          # 锚文本变更检测
}


class SemanticRecall:

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

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Dict[str, float]:
        """
        Returns:
            sem: {engineer_id: score} — 模块匹配分数
        """
        await self._ensure_module_cache()

        from ai.core import get_embed_client
        ec = await get_embed_client()
        q = self._build_query_text(ticket)
        qe = (await ec.embed(q)).tolist()

        # ── L2 语义召回 ──
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
            # 细分模块名 → module_classify 映射到类别 → 组「产品-类别」锚 key 匹配
            classify = self._config.module_classify or {}
            for eng in engineers:
                score = 0.0
                for prod, mods in eng.responsibility_modules.items():
                    cat_map = classify.get(prod, {})
                    for mod in mods:
                        cat = cat_map.get(mod)
                        key = f"{prod}-{cat}" if cat else None
                        if key and key in module_scores:
                            score = max(score, module_scores[key])
                if score > 0:
                    sem[eng.id] = score

        return sem


def invalidate_semantic_cache():
    global _cache
    _cache["module_embeddings"] = {}
    _cache["anchor_hash"] = ""

