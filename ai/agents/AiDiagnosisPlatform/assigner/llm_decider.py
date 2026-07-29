"""LLM 综合分析层：召回结果 + 工单信息 + 工程师画像 → LLM 直接决策"""

import json
import re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall import RecallResult
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
            response = await llm.complete(prompt)
            return self._parse(response, engineers)
        except Exception:
            return None

    def _build_prompt(self, ticket, engineers, recall_result, ranked_scores):
        lines = [
            "你是一名智能派单系统的决策专家。请根据以下工单信息、工程师画像和多路召回分数，推荐最适合处理该工单的工程师。",
            "", "【工单信息】",
            f"标题: {ticket.title or '无'}",
            f"问题描述: {ticket.problem_description}",
        ]
        if ticket.robot_type:
            lines.append(f"车型: {ticket.robot_type}")
        if ticket.required_skills:
            lines.append(f"所需技能: {', '.join(ticket.required_skills)}")
        if ticket.priority:
            lines.append(f"优先级: {ticket.priority}")

        lines.extend(["", "【候选工程师画像及召回分数】"])
        all_ids = set()
        all_ids.update(recall_result.module_recall.keys())
        all_ids.update(recall_result.external_history.keys())
        all_ids.update(recall_result.engineer_semantic.keys())
        all_ids.update(recall_result.history_semantic.keys())
        for eid in list(ranked_scores.keys())[:3]:
            all_ids.add(eid)

        emap = {e.id: e for e in engineers}
        for eid in sorted(all_ids):
            eng = emap.get(eid)
            if not eng:
                continue
            d = ranked_scores.get(eid, {})
            lines.append(f"工程师ID: {eng.id}")
            lines.append(f"  姓名: {eng.name}")
            lines.append(f"  职级: L{eng.job_level}")
            lines.append(f"  责任模块: {', '.join(eng.all_modules()) if eng.all_modules() else '无'}")
            if eng.duty_text:
                duty = eng.duty_text[:80] + "..." if len(eng.duty_text) > 80 else eng.duty_text
                lines.append(f"  职责简述: {duty}")
            lines.append(
                f"  召回分数: 模块={d.get('module_score', 0):.2f} "
                f"历史={d.get('history_score', 0):.2f} "
                f"语义={d.get('semantic_score', 0):.2f} "
                f"综合(含职级折扣)={d.get('total_score', 0):.2f}"
            )
            lines.append("")

        lines.extend([
            "【要求】",
            "1. 综合考虑工单内容、工程师技能、历史经验、语义相似度，推荐最合适的工程师",
            "2. 如果匹配度高可直接拍板，如果匹配度中等建议确认，如果匹配度低请标注兜底",
            "3. 输出必须是 JSON 格式",
            "", '{"engineer_id": "ID", "confidence_score": 0.85, "reasoning": "理由", "decision_type": "auto"}',
            "", "判定标准: >=0.8→auto, 0.5-0.8→recommend, <0.5→fallback",
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
