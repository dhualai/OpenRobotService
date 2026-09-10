"""精排评分层：三路并集，命中路取最高绝对分 + 职级折扣 + 部门 soft_prior

三路都用 0～1 绝对分，不按本批第一名拉满。
只命中一路：保留该路分数，不因其他路空而打折。
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

    def rank(
        self, recall_result: RecallResult,
        engineers: Optional[List[EngineerProfile]] = None,
        contact_assignee_id: Optional[str] = None,
        preferred_assignee_id: Optional[str] = None,
        creator_id: Optional[str] = None,
        prev_assignee_id: Optional[str] = None,
        dept_routing: Optional["DeptRoutingResult"] = None,
    ) -> Dict[str, Dict[str, float]]:
        llm_scores = recall_result.llm_recall or {}
        similar = recall_result.similar_recall or {}
        cluster = recall_result.cluster_recall or {}

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

        def _abs01(v: float) -> float:
            return round(min(1.0, max(0.0, float(v or 0.0))), 4)

        scores = {}
        for eid in ids:
            hit_llm = bool(eid in llm_scores and llm_scores.get(eid, 0) > 0)
            hit_similar = bool(eid in similar and similar.get(eid, 0) > 0)
            hit_cluster = bool(eid in cluster and cluster.get(eid, 0) > 0)
            llm = _abs01(llm_scores.get(eid, 0.0)) if hit_llm else 0.0
            sim = _abs01(similar.get(eid, 0.0)) if hit_similar else 0.0
            clu = _abs01(cluster.get(eid, 0.0)) if hit_cluster else 0.0
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
            was_rejected = eid in (getattr(recall_result, "misassign_rejected", None) or {})
            was_confirmed = eid in (getattr(recall_result, "misassign_confirmed", None) or {})
            if was_rejected:
                total *= 0.70

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
                "misassign_rejected": was_rejected,
                "misassign_confirmed": was_confirmed,
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
