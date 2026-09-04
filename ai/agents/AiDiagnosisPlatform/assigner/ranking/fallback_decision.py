"""决策与解释层：纯规则兜底"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import AssignmentResult, EngineerProfile


class FallbackDecision:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    def decide(self, ranked_scores, engineers):
        if not engineers:
            raise ValueError("工程师列表为空")
        if not ranked_scores:
            return self._fallback(engineers[0], "无任何匹配，默认兜底")

        top_id = next(iter(ranked_scores))
        top_score = ranked_scores[top_id]["total_score"]
        emap = {e.id: e for e in engineers}
        top_eng = emap.get(top_id)
        if top_eng is None:
            return self._fallback(engineers[0], f"Top-1 不在候选列表，强制兜底")

        t = self._config.decision_thresholds
        if top_score >= t.get("auto", 0.8):
            dt = "auto"
        elif top_score >= t.get("recommend", 0.5):
            dt = "recommend"
        else:
            dt = "fallback"

        return AssignmentResult(
            engineer_id=top_eng.id, engineer_name=top_eng.name,
            confidence_score=round(top_score, 4),
            reasoning=self._reasoning(dt, top_eng, ranked_scores[top_id], top_score),
            decision_type=dt,
        )

    def _reasoning(self, dt, eng, detail, conf):
        # 简洁一句话：不罗列候选、不展开多句，只点出核心依据（与 LLM 决策保持一致的精简风格）。
        dims = "/".join(
            f"{label}{detail.get(key, 0):.2f}"
            for key, label in [("llm_score", "LLM"), ("semantic_score", "语义"), ("history_score", "历史")]
            if detail.get(key, 0) > 0
        )
        dim_txt = f"（{dims} 综合占优）" if dims else ""
        if dt == "auto":
            return f"{eng.name} 精排分数最高{dim_txt}，自动指派。"
        if dt == "recommend":
            return f"{eng.name} 精排分数最高{dim_txt}，建议确认后派单。"
        return f"候选匹配度普遍偏低，按 {eng.name} 兜底派单，建议人工复核。"

    def _fallback(self, engineer, reason):
        return AssignmentResult(
            engineer_id=engineer.id, engineer_name=engineer.name,
            confidence_score=0.0, reasoning=reason, decision_type="fallback",
        )
