"""L3-B：已解决/已关闭工单自动聚簇 → 新单落入哪一簇 → 簇里谁常结单。

不再用手切「算法/前端/界面」问题域。单多了，簇自己长、自己拆。
派单时不现场重聚：缓存按历史记录哈希重建。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import load_history_records
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.recall.history_recall import time_decay
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

_cache = {
    "hash": "",
    "centroids": None,          # (C, D) 归一化
    "cluster_people": [],       # [{eid: {count, last_ts}}]
    "cluster_titles": [],       # 每簇 2～3 条代表标题，仅日志
}


def _as_ts(created_at) -> Optional[float]:
    if not created_at:
        return None
    try:
        if isinstance(created_at, (int, float)):
            return float(created_at)
        if isinstance(created_at, str):
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _ticket_text(rec: dict) -> str:
    return " ".join(filter(None, [
        (rec.get("title") or "").strip(),
        (rec.get("description") or "").strip(),
    ])).strip()


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(mat, axis=1, keepdims=True)
    n = np.maximum(n, 1e-12)
    return mat / n


def cluster_by_similarity(
    embs: np.ndarray,
    merge_threshold: float,
    min_size: int,
) -> List[List[int]]:
    """余弦 ≥ merge_threshold 的单并入同一簇；小于 min_size 的团丢掉。"""
    if embs.size == 0:
        return []
    vecs = _normalize_rows(np.asarray(embs, dtype=float))
    n = len(vecs)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    sims = vecs @ vecs.T
    for i in range(n):
        row = sims[i]
        for j in range(i + 1, n):
            if row[j] >= merge_threshold:
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [idx for idx in groups.values() if len(idx) >= min_size]


def cluster_centroids(embs: np.ndarray, groups: Sequence[Sequence[int]]) -> np.ndarray:
    vecs = _normalize_rows(np.asarray(embs, dtype=float))
    cents = []
    for idx in groups:
        mean = np.mean(vecs[list(idx)], axis=0)
        cents.append(mean)
    if not cents:
        return np.zeros((0, vecs.shape[1] if vecs.ndim == 2 else 0))
    return _normalize_rows(np.vstack(cents))


def count_term(count: float, exp: float = 0.3) -> float:
    """结单次数项：ln(1+n)^exp。exp<1 压低次数收益，默认 0.3。"""
    n = max(float(count), 0.0)
    e = max(float(exp), 0.0)
    base = float(np.log1p(n))
    if e == 1.0:
        return base
    return float(np.power(base, e)) if base > 0.0 else 0.0


def cluster_person_score(
    count: float,
    freshness: float,
    cluster_sim: float,
    exp: float = 0.3,
) -> float:
    """问题域人分：绝对 0～1，不按本批第一名拉满。

    次数项 × 时间新鲜度 × 簇相似度，超过 1 截断。
    弱命中停在 0.2～0.4，强命中才到 0.8～1.0。
    """
    raw = count_term(count, exp) * max(float(freshness), 0.0) * max(float(cluster_sim), 0.0)
    return round(min(1.0, raw), 4)


def pick_cluster_ids(
    query: np.ndarray,
    centroids: np.ndarray,
    assign_threshold: float,
    top_k: int,
) -> List[Tuple[int, float]]:
    """新单落到最近且过线的簇。认不出则空。"""
    if centroids.size == 0 or query.size == 0:
        return []
    q = np.asarray(query, dtype=float).reshape(-1)
    nrm = float(np.linalg.norm(q))
    if nrm <= 1e-12:
        return []
    q = q / nrm
    sims = centroids @ q
    order = np.argsort(-sims)
    out: List[Tuple[int, float]] = []
    for i in order:
        s = float(sims[int(i)])
        if s < assign_threshold:
            break
        out.append((int(i), s))
        if len(out) >= max(1, top_k):
            break
    return out


class ExpertiseRecall:
    """B路：自动簇上的结单实绩（已解决 + 已关闭）。"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        hc = self._config.history_recall or {}
        self._half_life_days = float(hc.get("half_life_days", 90))
        self._decay_floor = float(hc.get("decay_floor", 0.4))
        self._merge_threshold = float(hc.get("cluster_merge", 0.55))
        self._min_size = max(2, int(hc.get("cluster_min_size", 4)))
        self._assign_threshold = float(hc.get("cluster_assign", 0.40))
        self._cluster_top_k = max(1, int(hc.get("cluster_top_k", 2)))
        self._count_exp = float(hc.get("cluster_count_exp", 0.3))

    def _people_in_groups(
        self, recs: List[dict], groups: Sequence[Sequence[int]],
    ) -> Tuple[List[dict], List[List[str]]]:
        people: List[dict] = []
        titles: List[List[str]] = []
        for idx in groups:
            tbl: Dict[str, dict] = {}
            seen_titles: List[str] = []
            for i in idx:
                rec = recs[i]
                eid = (rec.get("engineer_id") or "").strip()
                title = (rec.get("title") or "").strip()
                if title and title not in seen_titles and len(seen_titles) < 3:
                    seen_titles.append(title)
                if not eid:
                    continue
                ts = _as_ts(rec.get("created_at")) or 0.0
                entry = tbl.setdefault(eid, {"count": 0, "last_ts": 0.0})
                entry["count"] += 1
                if ts > entry["last_ts"]:
                    entry["last_ts"] = ts
            people.append(tbl)
            titles.append(seen_titles)
        return people, titles

    async def _ensure_cache(self) -> None:
        global _cache
        recs = load_history_records(self._config.module_keywords or {})
        if not recs:
            _cache.update(hash="", centroids=None, cluster_people=[], cluster_titles=[])
            return

        import hashlib, json
        h = hashlib.md5(
            json.dumps(recs, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        if _cache["hash"] == h and _cache["centroids"] is not None:
            return

        texts = [_ticket_text(r) for r in recs]
        keep = [i for i, t in enumerate(texts) if t]
        if len(keep) < self._min_size:
            logger.info(f"[expertise_recall] 可向量化工单不足 {self._min_size}，B 路空")
            _cache.update(hash=h, centroids=np.zeros((0, 1)), cluster_people=[], cluster_titles=[])
            return

        slim_recs = [recs[i] for i in keep]
        slim_texts = [texts[i] for i in keep]
        try:
            from ai.core import get_embed_client
            ec = await get_embed_client()
            raw = await ec.embed_batch(slim_texts, normalize=True)
        except Exception as e:
            logger.warning(f"[expertise_recall] 历史单向量化失败，B 路空: {e}")
            _cache.update(hash=h, centroids=np.zeros((0, 1)), cluster_people=[], cluster_titles=[])
            return

        embs = np.vstack([np.asarray(v, dtype=float) for v in raw])
        groups = cluster_by_similarity(embs, self._merge_threshold, self._min_size)
        cents = cluster_centroids(embs, groups)
        people, titles = self._people_in_groups(slim_recs, groups)
        _cache.update(
            hash=h, centroids=cents, cluster_people=people, cluster_titles=titles,
        )
        logger.info(
            f"[expertise_recall] 自动簇完成: 单={len(slim_recs)} 簇={len(groups)} "
            f"（合并阈值={self._merge_threshold} 最小团={self._min_size}）"
        )

    def _score_people(self, cluster_hits: List[Tuple[int, float]]) -> Dict[str, float]:
        raw: Dict[str, float] = {}
        people = _cache.get("cluster_people") or []
        for cid, sim in cluster_hits:
            if cid < 0 or cid >= len(people):
                continue
            for eid, entry in (people[cid] or {}).items():
                last_ts = float(entry.get("last_ts") or 0.0)
                freshness = (
                    time_decay(last_ts, self._half_life_days, self._decay_floor)
                    if last_ts else 1.0
                )
                raw[eid] = raw.get(eid, 0.0) + cluster_person_score(
                    entry.get("count", 0), freshness, sim, self._count_exp,
                )
        if not raw:
            return {}
        return {eid: min(1.0, round(v, 4)) for eid, v in raw.items()}

    async def arecall(self, ticket: TicketContext) -> Dict[str, float]:
        await self._ensure_cache()
        cents = _cache.get("centroids")
        if cents is None or getattr(cents, "size", 0) == 0:
            return {}

        q = " ".join(filter(None, [
            ticket.title or "",
            ticket.problem_description or "",
        ])).strip()
        if not q:
            return {}
        try:
            from ai.core import get_embed_client
            ec = await get_embed_client()
            qe = await ec.embed(q, normalize=True)
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] Step3-L3-B 新单向量化失败: {e}")
            return {}

        hits = pick_cluster_ids(
            np.asarray(qe, dtype=float),
            np.asarray(cents, dtype=float),
            self._assign_threshold,
            self._cluster_top_k,
        )
        if not hits:
            logger.debug(f"[派单:{ticket.id}] Step3-L3-B 自动簇: 未落入任何簇")
            return {}

        titles = _cache.get("cluster_titles") or []
        desc = []
        for cid, sim in hits:
            reps = " / ".join((titles[cid] if cid < len(titles) else [])[:2]) or "-"
            desc.append(f"#{cid}({sim:.2f}|{reps})")
        scores = self._score_people(hits)
        logger.debug(
            f"[派单:{ticket.id}] Step3-L3-B 自动簇: 落入[{', '.join(desc)}] 聚人={len(scores)}"
        )
        return scores


def invalidate_expertise_cache():
    global _cache
    _cache = {
        "hash": "",
        "centroids": None,
        "cluster_people": [],
        "cluster_titles": [],
    }
