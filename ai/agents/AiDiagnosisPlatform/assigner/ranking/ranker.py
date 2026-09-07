"""精排评分层：三路并集，命中路取最高归一分 + 职级折扣 + 部门 soft_prior

只命中一路：保留该路归一分，不因其他路空而打折。
多路命中：取最高，来源标签仍标出命中了哪几路。
"""

from typing import Dict, List, Optional, TYPE_CHECKING

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile

if TYPE_CHECKING:
    from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import DeptRoutingResult


class Ranker:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._penalty: Dict[int, float] = self._config.job_level_penalty
        try:
            self._preferred_floor = float(getattr(self._config, "preferred_floor", 0.9))
        except (TypeError, ValueError):
            self._preferred_floor = 0.9

    @staticmethod
    def _apply_transfer_signals(
        llm_scores: Dict[str, float],
        transfer_signals: Optional[Dict],
    ) -> Dict[str, float]:
        """转派旁路：先改 L1 分再加权。本版 signals 恒空，有值时 delta 可正可负。"""
        out = dict(llm_scores or {})
        if not transfer_signals or not isinstance(transfer_signals, dict):
            return out
        for key in ("penalties", "boosts"):
            bucket = transfer_signals.get(key) or {}
            if not isinstance(bucket, dict):
                continue
            for eid, spec in bucket.items():
                if not eid:
                    continue
                try:
                    delta = float((spec or {}).get("delta") or 0.0)
                except (TypeError, ValueError):
                    delta = 0.0
                out[eid] = max(0.0, out.get(eid, 0.0) + delta)
        return out

    def rank(
        self, recall_result: RecallResult,
        engineers: Optional[List[EngineerProfile]] = None,
        contact_assignee_id: Optional[str] = None,
        preferred_assignee_id: Optional[str] = None,
        creator_id: Optional[str] = None,
        prev_assignee_id: Optional[str] = None,
        dept_routing: Optional["DeptRoutingResult"] = None,
    ) -> Dict[str, Dict[str, float]]:
        llm_scores = self._apply_transfer_signals(
            recall_result.llm_recall,
            getattr(recall_result, "transfer_signals", None),
        )
        similar = getattr(recall_result, "similar_recall", None) or recall_result.history_recall or {}
        cluster = getattr(recall_result, "cluster_recall", None) or {}

        ids = set()
        ids.update(llm_scores.keys())
        ids.update(similar.keys())
        ids.update(cluster.keys())
        cand_ids = {e.id for e in engineers} if engineers else set()
        for _cid in (contact_assignee_id, preferred_assignee_id, creator_id, prev_assignee_id):
            if _cid and _cid in cand_ids:
                ids.add(_cid)

        level_map: Dict[str, int] = {}
        eng_map: Dict[str, EngineerProfile] = {}
        dept_people: Dict[str, int] = {}
        if engineers:
            for e in engineers:
                level_map[e.id] = e.job_level
                eng_map[e.id] = e
                dept = e.department or ""
                dept_people[dept] = dept_people.get(dept, 0) + 1

        dept_boost = 1.0
        primary_dept = ""
        if dept_routing and dept_routing.mode == "soft_prior" and dept_routing.primary_dept:
            routing_cfg = getattr(self._config, "department_routing", {}) or {}
            thresholds = routing_cfg.get("thresholds") or {}
            dept_boost = float(thresholds.get("dept_boost", 1.5))
            primary_dept = dept_routing.primary_dept

        def _max_of(recall: Dict[str, float]) -> float:
            vals = [recall.get(eid, 0.0) for eid in ids]
            return max(vals) if vals else 0.0

        def _norm(v: float, m: float) -> float:
            return round(v / m, 4) if m > 0 else 0.0

        llm_max, sim_max, clu_max = _max_of(llm_scores), _max_of(similar), _max_of(cluster)

        scores = {}
        for eid in ids:
            hit_llm = bool(eid in llm_scores and llm_scores.get(eid, 0) > 0)
            hit_similar = bool(eid in similar and similar.get(eid, 0) > 0)
            hit_cluster = bool(eid in cluster and cluster.get(eid, 0) > 0)
            llm = _norm(llm_scores.get(eid, 0.0), llm_max) if hit_llm else 0.0
            # 相似工单 / 问题域已是绝对 0～1，不再按本批最高拉满
            sim = min(1.0, max(0.0, float(similar.get(eid, 0.0)))) if hit_similar else 0.0
            clu = min(1.0, max(0.0, float(cluster.get(eid, 0.0)))) if hit_cluster else 0.0
            hit_vals = []
            if hit_llm:
                hit_vals.append(llm)
            if hit_similar:
                hit_vals.append(sim)
            if hit_cluster:
                hit_vals.append(clu)
            raw = max(hit_vals) if hit_vals else 0.0
            hit_count = len(hit_vals)

            lv = level_map.get(eid, 1)
            dept = (eng_map.get(eid) or EngineerProfile(id=eid, name="")).department or ""
            only_one_in_dept = dept_people.get(dept, 0) <= 1
            if only_one_in_dept and lv > 1:
                mul = 0.90
            else:
                mul = self._penalty.get(lv, self._penalty.get(99, 0.6))

            is_contact = bool(contact_assignee_id and eid == contact_assignee_id)
            is_preferred = bool(preferred_assignee_id and eid == preferred_assignee_id)

            dept_mul = dept_boost if (
                primary_dept and (eng_map.get(eid) or EngineerProfile(id=eid, name="")).department == primary_dept
            ) else 1.0

            total = raw * mul * dept_mul
            if is_preferred and self._preferred_floor > 0:
                total = max(total, self._preferred_floor)

            is_creator = bool(creator_id and eid == creator_id)
            is_prev = bool(prev_assignee_id and eid == prev_assignee_id)

            scores[eid] = {
                "llm_score": llm,
                "similar_score": sim,
                "cluster_score": clu,
                "history_score": sim,
                "hit_llm": hit_llm,
                "hit_similar": hit_similar,
                "hit_cluster": hit_cluster,
                "hit_count": hit_count,
                "outside_tighten": bool(engineers) and eid not in cand_ids,
                "raw_total": round(raw, 4), "job_level": lv,
                "level_multiplier": mul, "contact_assignee": is_contact,
                "preferred_assignee": is_preferred,
                "is_creator": is_creator,
                "prev_unsatisfied": is_prev,
                "contact_multiplier": 1.0,
                "preferred_floor": round(self._preferred_floor, 3) if is_preferred else None,
                "dept_multiplier": round(dept_mul, 3),
                "total_score": round(total, 4),
            }
        return dict(sorted(
            scores.items(),
            key=lambda x: (
                x[1]["total_score"],
                x[1].get("hit_count", 0),
                x[1].get("llm_score", 0),
                x[1].get("similar_score", 0),
                x[1].get("cluster_score", 0),
            ),
            reverse=True,
        ))
