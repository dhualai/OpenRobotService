"""决策与解释层：Top-1 输出 + decision_type 判定（纯规则，无 LLM）"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import AssignmentResult, EngineerProfile


class DecisionMaker:
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
            return self._fallback(engineers[0], f"Top-1 工程师不在候选列表，强制兜底")

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
                    f"- {eng.name}({eid}): 置信度 {d['total_score']:.2f} "
                    f"(L{d.get('job_level', '?')}), {'|'.join(prod_parts)}"
                )
        return "\n".join(lines) or "无其他候选"

    def _reasoning(self, dt, eng, detail, conf, runner_up):
        reasons = []
        m = detail.get("module_score", 0)
        h = detail.get("history_score", 0)
        s = detail.get("semantic_score", 0)
        if m > 0:
            reasons.append(f"责任模块匹配({m:.2f}): {', '.join(eng.all_modules())}")
        if h > 0:
            reasons.append(f"历史工单匹配({h:.2f})")
        if s > 0:
            reasons.append(f"语义相似度({s:.2f})")
        base = f"推荐 {eng.name}，综合置信度 {conf:.2f}。"
        if reasons:
            base += "依据: " + "; ".join(reasons) + "。"
        if dt == "auto":
            return base + "匹配度高，可直接派单。"
        elif dt == "recommend":
            return base + f"匹配度中等，建议确认。其他候选:\n{runner_up}"
        return base + "匹配度较低，已做兜底派单，建议人工复核。其他候选:\n" + runner_up

    def _fallback(self, engineer, reason):
        return AssignmentResult(
            engineer_id=engineer.id, engineer_name=engineer.name,
            confidence_score=0.0, reasoning=reason, decision_type="fallback",
        )
