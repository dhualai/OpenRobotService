"""决策与解释层：Top-1 输出 + decision_type 判定（纯规则，无 LLM）"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import AssignmentResult, EngineerProfile


class DecisionMaker:
    """第四层：决策与解释（纯规则回退）"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    def decide(
        self,
        ranked_scores: Dict[str, Dict[str, float]],
        engineers: List[EngineerProfile],
    ) -> AssignmentResult:
        """根据精排结果做出最终决策（规则回退）。"""
        if not engineers:
            raise ValueError("工程师列表为空，无法派单")

        if not ranked_scores:
            return self._fallback_result(engineers[0], "无任何匹配，默认兜底")

        top_id = next(iter(ranked_scores))
        top_score = ranked_scores[top_id]["total_score"]

        engineer_map = {e.id: e for e in engineers}
        top_eng = engineer_map.get(top_id)
        if top_eng is None:
            return self._fallback_result(
                engineers[0], f"Top-1 工程师 {top_id} 不在候选列表，强制兜底"
            )

        thresholds = self._config.decision_thresholds
        auto_threshold = thresholds.get("auto", 0.8)
        recommend_threshold = thresholds.get("recommend", 0.5)

        if top_score >= auto_threshold:
            decision_type = "auto"
        elif top_score >= recommend_threshold:
            decision_type = "recommend"
        else:
            decision_type = "fallback"

        reasoning = self._rule_reasoning(
            decision_type=decision_type,
            engineer=top_eng,
            score_detail=ranked_scores[top_id],
            confidence=top_score,
            runner_up=self._get_runner_up(ranked_scores, engineer_map),
        )

        return AssignmentResult(
            engineer_id=top_eng.id,
            engineer_name=top_eng.name,
            confidence_score=round(top_score, 4),
            reasoning=reasoning,
            decision_type=decision_type,
        )

    def _get_runner_up(
        self,
        ranked_scores: Dict[str, Dict[str, float]],
        engineer_map: Dict[str, EngineerProfile],
    ) -> str:
        """获取 Top-2/3 候选信息，用于 recommend 模式。"""
        items = list(ranked_scores.items())
        if len(items) < 2:
            return "无其他候选"
        lines = []
        for eid, detail in items[1:3]:
            eng = engineer_map.get(eid)
            if eng:
                lines.append(
                    f"- {eng.name}({eid}): 置信度 {detail['total_score']:.2f}, "
                    f"技能 {eng.skills}, 模块 {eng.responsibility_modules}"
                )
        return "\n".join(lines) if lines else "无其他候选"

    def _rule_reasoning(
        self,
        decision_type: str,
        engineer: EngineerProfile,
        score_detail: Dict[str, float],
        confidence: float,
        runner_up: str,
    ) -> str:
        """基于规则生成推荐理由。"""
        skill = score_detail.get("skill_score", 0.0)
        module = score_detail.get("module_score", 0.0)
        history = score_detail.get("history_score", 0.0)
        semantic = score_detail.get("semantic_score", 0.0)

        reasons = []
        if module > 0:
            reasons.append(
                f"责任模块匹配({module:.2f}): {', '.join(engineer.responsibility_modules)}"
            )
        if skill > 0:
            reasons.append(f"技能标签匹配({skill:.2f}): {', '.join(engineer.skills)}")
        if history > 0:
            reasons.append(f"历史工单匹配({history:.2f})")
        if semantic > 0:
            reasons.append(f"语义相似度({semantic:.2f})")

        base = f"推荐 {engineer.name}，综合置信度 {confidence:.2f}。"
        if reasons:
            base += "依据: " + "; ".join(reasons) + "。"

        if decision_type == "auto":
            return base + "匹配度高，可直接派单。"
        elif decision_type == "recommend":
            return base + f"匹配度中等，建议确认。其他候选:\n{runner_up}"
        else:
            return (
                base
                + "匹配度较低，已做兜底派单，建议人工复核。其他候选:\n"
                + runner_up
            )

    def _fallback_result(self, engineer: EngineerProfile, reason: str) -> AssignmentResult:
        """兜底派单结果。"""
        return AssignmentResult(
            engineer_id=engineer.id,
            engineer_name=engineer.name,
            confidence_score=0.0,
            reasoning=reason,
            decision_type="fallback",
        )
