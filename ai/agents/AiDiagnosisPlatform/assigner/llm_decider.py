"""LLM 综合决策层：三路召回分数 + 全员画像 → LLM 拍板"""

import json, re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult, EngineerProfile, TicketContext,
)


class LlmDecider:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    async def adecide(self, ticket, engineers, recall_result, ranked_scores):
        prompt = self._build_prompt(ticket, engineers, recall_result, ranked_scores)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=400, temperature=0.3)
            return self._parse(response, engineers)
        except Exception:
            return None

    def _build_prompt(self, ticket, engineers, recall_result, ranked_scores):
        lines = [
            "你是派单决策专家。根据工单、工程师画像和三路召回分数，推荐最合适的人。",
            "",
            "【工单】",
            f"标题: {ticket.title or '无'}",
            f"描述: {ticket.problem_description}",
        ]
        if ticket.robot_type:
            lines.append(f"车型: {ticket.robot_type}")
        if ticket.fault_code:
            lines.append(f"故障码: {ticket.fault_code}")

        lines.extend(["", "【候选人排名（已含职级折扣）】"])

        emap = {e.id: e for e in engineers}
        for rank, (eid, d) in enumerate(list(ranked_scores.items())[:5], 1):
            eng = emap.get(eid)
            if not eng:
                continue
            prod_parts = []
            for p, mods in eng.responsibility_modules.items():
                prod_parts.append(f"[{p}]{','.join(mods)}" if mods else f"[{p}]")
            duty = (eng.duty_text or "")[:100]
            dep = f"({eng.department})" if eng.department else ""
            lines.append(
                f"#{rank} {eng.name} {dep} L{eng.job_level} "
                f"|{'|'.join(prod_parts)}"
            )
            lines.append(
                f"   分数: 总={d.get('total_score',0):.2f} "
                f"LLM={d.get('llm_score',0):.2f} 语义={d.get('semantic_score',0):.2f} "
                f"历史={d.get('history_score',0):.2f}"
            )
            if duty:
                lines.append(f"   职责: {duty}")

        lines.extend([
            "",
            "输出 JSON:",
            '{"engineer_id":"...", "confidence_score":0.85, "reasoning":"理由", "decision_type":"auto"}',
            "decision_type: auto(>=0.8) / recommend(0.5-0.8) / fallback(<0.5)",
        ])
        return "\n".join(lines)

    def _parse(self, response, engineers):
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
        eid = data.get("engineer_id", "")
        eng = next((e for e in engineers if e.id == eid), None)
        if not eng:
            return None
        dt = data.get("decision_type", "fallback").strip().lower()
        return AssignmentResult(
            engineer_id=eng.id, engineer_name=eng.name,
            confidence_score=round(float(data.get("confidence_score", 0.0)), 4),
            reasoning=data.get("reasoning", "").strip(),
            decision_type=dt if dt in ("auto", "recommend", "fallback") else "fallback",
        )
