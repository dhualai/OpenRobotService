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
            reasoning=self._reasoning(dt, top_eng, ranked_scores[top_id], top_score,
                                      self._runner_up(ranked_scores, emap)),
            decision_type=dt,
        )

    def _runner_up(self, scores, emap):
        items = list(scores.items())
        if len(items) < 2:
            return "无其他候选"
        lines = []
        for eid, d in items[1:3]:
            eng = emap.get(eid)
            if eng:
                prod_parts = [f"[{p}]{','.join(m) if m else ''}" for p, m in eng.responsibility_modules.items()]
                lines.append(
                    f"- {eng.name}(L{d.get('job_level','?')}): "
                    f"{d['total_score']:.2f} {'|'.join(prod_parts)}"
                )
        return "\n".join(lines) or "无其他候选"

    def _reasoning(self, dt, eng, detail, conf, runner_up):
        reasons = []
        for key, label in [("llm_score", "LLM"), ("semantic_score", "语义"), ("history_score", "历史")]:
            v = detail.get(key, 0)
            if v > 0:
                reasons.append(f"{label}({v:.2f})")
        base = f"推荐 {eng.name}，置信度 {conf:.2f}。"
        if reasons:
            base += f" 维度: {'/'.join(reasons)}。"
        if dt == "auto":
            return base + "可直接派单。"
        elif dt == "recommend":
            return base + f"建议确认。候选:\n{runner_up}"
        return base + "兜底派单，建议复核。候选:\n" + runner_up

    def _fallback(self, engineer, reason):
        return AssignmentResult(
            engineer_id=engineer.id, engineer_name=engineer.name,
            confidence_score=0.0, reasoning=reason, decision_type="fallback",
        )
