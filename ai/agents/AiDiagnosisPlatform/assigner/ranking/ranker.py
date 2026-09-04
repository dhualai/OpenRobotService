"""精排评分层：三路加权 + 职级折扣 + 部门 soft_prior"""

from typing import Dict, List, Optional, TYPE_CHECKING

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile

if TYPE_CHECKING:
    from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import DeptRoutingResult


class Ranker:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        w = self._config.ranker_weights
        self._w_llm = w.get("llm_match", 0.30)
        self._w_semantic = w.get("semantic_match", 0.35)
        self._w_history = w.get("history_match", 0.10)
        self._penalty: Dict[int, float] = self._config.job_level_penalty
        # 项目对接人加权系数（≥1；=1 不加权）。默认 2.0，可由 config.contact_bonus 覆盖。
        try:
            self._contact_bonus = float(getattr(self._config, "contact_bonus", 2.0))
        except (TypeError, ValueError):
            self._contact_bonus = 2.0
        # 对接人/倾向人 精排保底分：×contact_bonus 后仍低于该值则抬到该值（默认 0.8），
        # 保证其大概率进入 Step6 决策窗口。可由 config.contact_floor 覆盖。
        try:
            self._contact_floor = float(getattr(self._config, "contact_floor", 0.8))
        except (TypeError, ValueError):
            self._contact_floor = 0.8

    def rank(
        self, recall_result: RecallResult,
        engineers: Optional[List[EngineerProfile]] = None,
        contact_assignee_id: Optional[str] = None,
        preferred_assignee_id: Optional[str] = None,
        creator_id: Optional[str] = None,
        dept_routing: Optional["DeptRoutingResult"] = None,
    ) -> Dict[str, Dict[str, float]]:
        ids = set()
        ids.update(recall_result.llm_recall.keys())
        ids.update(recall_result.semantic_recall.keys())
        ids.update(recall_result.history_recall.keys())
        # 把 项目对接人 / 用户倾向处理人 也纳入精排（即使三路召回都未命中），
        # 使其享受 contact_bonus 加权 + contact_floor 保底，并进入决策窗口被 LLM 看到。
        # 仅当该人在候选工程师集合(candidates)内才加入，避免引入不在候选的人。
        cand_ids = {e.id for e in engineers} if engineers else set()
        for _cid in (contact_assignee_id, preferred_assignee_id):
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

        # ── 各路召回分数归一化（max=1）──
        # 三路分数天然尺度不同（LLM 置信 0~1 / 语义 cos / 历史聚合值），
        # 直接加权相加会因尺度差异失真。此处对每路在全体候选内做 max 归一化，
        # 使各路对齐到 0~1 后再按 ranker_weights 加权，可信度更稳。
        def _max_of(recall: Dict[str, float]) -> float:
            vals = [recall.get(eid, 0.0) for eid in ids]
            return max(vals) if vals else 0.0

        llm_max = _max_of(recall_result.llm_recall)
        sem_max = _max_of(recall_result.semantic_recall)
        his_max = _max_of(recall_result.history_recall)

        def _norm(v: float, m: float) -> float:
            return round(v / m, 4) if m > 0 else 0.0

        scores = {}
        for eid in ids:
            llm = _norm(recall_result.llm_recall.get(eid, 0.0), llm_max)
            sem = _norm(recall_result.semantic_recall.get(eid, 0.0), sem_max)
            his = _norm(recall_result.history_recall.get(eid, 0.0), his_max)

            raw = self._w_llm * llm + self._w_semantic * sem + self._w_history * his

            lv = level_map.get(eid, 1)
            dept = (eng_map.get(eid) or EngineerProfile(id=eid, name="")).department or ""
            # 部门内只有一人时，不打折（如机器人事业部只有文永翔 L2）
            only_one_in_dept = dept_people.get(dept, 0) <= 1
            if only_one_in_dept and lv > 1:
                mul = 0.90  # 轻微折扣，但不被其他部门的 L1 淹没
            else:
                mul = self._penalty.get(lv, self._penalty.get(99, 0.6))

            # 加权：项目对接人 或 用户倾向处理人 命中 → total × contact_bonus（默认2.0）。
            # 同一人同时是对接人又是倾向处理人时不重复乘（只 × 一次），避免加权过度。
            is_contact = bool(contact_assignee_id and eid == contact_assignee_id)
            is_preferred = bool(preferred_assignee_id and eid == preferred_assignee_id)
            is_special = is_contact or is_preferred
            contact_mul = self._contact_bonus if is_special else 1.0

            dept_mul = dept_boost if (
                primary_dept and (eng_map.get(eid) or EngineerProfile(id=eid, name="")).department == primary_dept
            ) else 1.0

            # 保底：对接人/倾向人 先乘 contact_bonus，再抬到 contact_floor（默认 0.8），
            # 避免其基础分低导致翻倍后仍进不了 Step6 决策窗口。普通候选不保底。
            total = raw * mul * contact_mul * dept_mul
            if is_special and self._contact_floor > 1.0:
                total = max(total, self._contact_floor)

            # 自提单人标识：不排除提单人，仅标记"这是提单人（自己提的单）"，交由 LLM 判断可否接单。
            is_creator = bool(creator_id and eid == creator_id)

            scores[eid] = {
                "llm_score": llm, "semantic_score": sem,
                "history_score": his,
                "raw_total": round(raw, 4), "job_level": lv,
                "level_multiplier": mul, "contact_assignee": is_contact,
                "preferred_assignee": is_preferred,
                "is_creator": is_creator,
                "contact_multiplier": round(contact_mul, 3),
                "contact_floor": round(self._contact_floor, 3) if is_special else None,
                "dept_multiplier": round(dept_mul, 3),
                "total_score": round(total, 4),
            }
        return dict(sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True))
