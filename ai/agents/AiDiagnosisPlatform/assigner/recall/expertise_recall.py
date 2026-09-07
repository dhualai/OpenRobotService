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
    "cluster_tickets": [],      # [[{ticket_id, title, engineer_id}]]
    "ticket_points": [],        # 二维投影，给开发者模式散点图
    "ticket_total": 0,          # 参与聚簇的历史单数（含未入簇噪声）
}


def _blank_cache(h: str = ""):
    return {
        "hash": h,
        "centroids": None,
        "cluster_people": [],
        "cluster_titles": [],
        "cluster_tickets": [],
        "ticket_points": [],    # [{ticket_id, title, engineer_id, cluster_id, x, y}]
        "ticket_total": 0,
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
    raw = [idx for idx in groups.values() if len(idx) >= min_size]
    return tighten_clusters(vecs, raw, merge_threshold, min_size)


def tighten_clusters(
    embs: np.ndarray,
    groups: Sequence[Sequence[int]],
    cohesion: float,
    min_size: int,
) -> List[List[int]]:
    """单链接会把 A≈B、B≈C 串成一团。这里要求每张单都靠近簇中心，否则踢出去。"""
    vecs = _normalize_rows(np.asarray(embs, dtype=float))
    kept: List[List[int]] = []
    for idx in groups:
        members = [int(i) for i in idx]
        while len(members) >= min_size:
            cent = np.mean(vecs[members], axis=0)
            norm = float(np.linalg.norm(cent)) or 1e-12
            cent = cent / norm
            sims = vecs[members] @ cent
            nxt = [members[i] for i, s in enumerate(sims) if float(s) >= cohesion]
            if len(nxt) == len(members):
                kept.append(members)
                break
            members = nxt
    return kept


def project_to_2d(embs: np.ndarray) -> np.ndarray:
    """把高维工单向量投到二维，方便看簇是散是糊。轴没有业务含义。"""
    mat = np.asarray(embs, dtype=float)
    if mat.ndim != 2 or mat.shape[0] == 0:
        return np.zeros((0, 2))
    if mat.shape[0] == 1 or mat.shape[1] == 0:
        return np.zeros((mat.shape[0], 2))
    centered = mat - mat.mean(axis=0)
    rank = min(2, centered.shape[0] - 1, centered.shape[1])
    if rank < 1:
        return np.zeros((mat.shape[0], 2))
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    xy = centered @ vt[:rank].T
    if rank == 1:
        xy = np.column_stack([np.ravel(xy), np.zeros(len(xy))])
    scale = float(np.max(np.abs(xy))) or 1.0
    return np.round(xy / scale, 4)


def build_ticket_points(
    recs: Sequence[dict],
    groups: Sequence[Sequence[int]],
    xy: np.ndarray,
) -> List[dict]:
    belong: Dict[int, int] = {}
    for cid, idx in enumerate(groups):
        for i in idx:
            belong[int(i)] = cid
    points: List[dict] = []
    for i, rec in enumerate(recs):
        eid = (rec.get("engineer_id") or "").strip()
        points.append({
            "ticket_id": str(rec.get("ticket_id") or ""),
            "title": (rec.get("title") or "").strip(),
            "engineer_id": eid,
            "cluster_id": belong.get(i, -1),
            "x": float(xy[i, 0]) if i < len(xy) else 0.0,
            "y": float(xy[i, 1]) if i < len(xy) else 0.0,
        })
    return points


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
    ) -> Tuple[List[dict], List[List[str]], List[List[dict]]]:
        people: List[dict] = []
        titles: List[List[str]] = []
        tickets: List[List[dict]] = []
        for idx in groups:
            tbl: Dict[str, dict] = {}
            seen_titles: List[str] = []
            items: List[dict] = []
            for i in idx:
                rec = recs[i]
                eid = (rec.get("engineer_id") or "").strip()
                title = (rec.get("title") or "").strip()
                items.append({
                    "ticket_id": str(rec.get("ticket_id") or ""),
                    "title": title,
                    "engineer_id": eid,
                })
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
            tickets.append(items)
        return people, titles, tickets

    async def _ensure_cache(self) -> None:
        global _cache
        recs = load_history_records(self._config.module_keywords or {})
        if not recs:
            _cache.update(_blank_cache(""))
            return

        import hashlib, json
        h = hashlib.md5(
            json.dumps(recs, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        if (
            _cache["hash"] == h
            and _cache["centroids"] is not None
            and _cache.get("ticket_points")
        ):
            return

        texts = [_ticket_text(r) for r in recs]
        keep = [i for i, t in enumerate(texts) if t]
        if len(keep) < self._min_size:
            logger.info(f"[expertise_recall] 可向量化工单不足 {self._min_size}，B 路空")
            empty = _blank_cache(h)
            empty["centroids"] = np.zeros((0, 1))
            empty["ticket_total"] = len(keep)
            _cache.update(empty)
            return

        slim_recs = [recs[i] for i in keep]
        slim_texts = [texts[i] for i in keep]
        try:
            from ai.core import get_embed_client
            ec = await get_embed_client()
            raw = await ec.embed_batch(slim_texts, normalize=True)
        except Exception as e:
            logger.warning(f"[expertise_recall] 历史单向量化失败，B 路空: {e}")
            empty = _blank_cache(h)
            empty["centroids"] = np.zeros((0, 1))
            empty["ticket_total"] = len(slim_recs)
            _cache.update(empty)
            return

        embs = np.vstack([np.asarray(v, dtype=float) for v in raw])
        groups = cluster_by_similarity(embs, self._merge_threshold, self._min_size)
        cents = cluster_centroids(embs, groups)
        people, titles, tickets = self._people_in_groups(slim_recs, groups)
        points = build_ticket_points(slim_recs, groups, project_to_2d(embs))
        _cache.update(
            hash=h,
            centroids=cents,
            cluster_people=people,
            cluster_titles=titles,
            cluster_tickets=tickets,
            ticket_points=points,
            ticket_total=len(slim_recs),
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
    _cache = _blank_cache("")


def cluster_snapshot_from_cache(name_by_id: Optional[Dict[str, str]] = None) -> dict:
    """当前进程里的簇快照。不触发向量化；缓存空则 ready=false。"""
    names = name_by_id or {}
    people = _cache.get("cluster_people") or []
    titles = _cache.get("cluster_titles") or []
    tickets = _cache.get("cluster_tickets") or []
    total = int(_cache.get("ticket_total") or 0)
    clusters = []
    clustered = 0
    for i, tbl in enumerate(people):
        items = tickets[i] if i < len(tickets) else []
        clustered += len(items)
        members = []
        for eid, entry in sorted(
            (tbl or {}).items(),
            key=lambda kv: (-int(kv[1].get("count") or 0), kv[0]),
        ):
            members.append({
                "engineer_id": eid,
                "name": names.get(eid) or eid,
                "count": int(entry.get("count") or 0),
            })
        shown = []
        for t in items[:40]:
            eid = t.get("engineer_id") or ""
            shown.append({
                **t,
                "engineer_name": names.get(eid) or eid,
            })
        clusters.append({
            "id": i,
            "titles": titles[i] if i < len(titles) else [],
            "ticket_count": len(items),
            "people": members,
            "tickets": shown,
        })
    cents = _cache.get("centroids")
    ready = bool(_cache.get("hash") and cents is not None)
    points = []
    for p in _cache.get("ticket_points") or []:
        eid = p.get("engineer_id") or ""
        points.append({
            **p,
            "engineer_name": names.get(eid) or eid,
        })
    return {
        "ready": ready,
        "ticket_total": total,
        "clustered": clustered,
        "noise": max(0, total - clustered),
        "clusters": clusters,
        "points": points,
    }


async def rebuild_cluster_cache() -> dict:
    """清历史单缓存和簇缓存，再现场聚一次。"""
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import invalidate_cache
    invalidate_cache()
    invalidate_expertise_cache()
    rec = ExpertiseRecall()
    await rec._ensure_cache()
    return cluster_snapshot_from_cache()
