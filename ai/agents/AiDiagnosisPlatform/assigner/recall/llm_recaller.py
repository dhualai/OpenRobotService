"""L1 纯LLM召回：工单 + 全员画像 → LLM 直接推荐 Top-K

这是三路召回中语义理解最强的一路。LLM 能同时看到所有人的 duty_text
和 responsibility_modules，理解模糊边界（"这个人主要负责地图但也参与后端"）。
"""

import json, re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

_MAX_ENGINEERS = 30  # 超过此人数据截断防止 token 溢出


class LlmRecaller:
    """纯 LLM 召回——让模型直接看全员画像做第一轮排序"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Dict[str, float]:
        """LLM 直接评估所有候选人并返回置信度分数。"""
        if not engineers:
            return {}

        prompt = self._build_prompt(ticket, engineers)

        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=600, temperature=0.3)
            return self._parse(response, engineers)
        except Exception as e:
            logger.warning(f"[llm_recall] LLM召回失败: {e}")
            return {}

    def _build_prompt(self, ticket, engineers):
        lines = [
            "你是派单专家。请根据工单内容，评估每位候选工程师的匹配度（0~1）。",
            "综合考虑：责任模块是否对口、职责描述是否匹配、过往经验是否相关。",
            "",
            "【工单】",
            f"标题: {ticket.title or '无'}",
            f"描述: {ticket.problem_description}",
        ]
        if ticket.robot_type:
            lines.append(f"车型: {ticket.robot_type}")
        if ticket.fault_code:
            lines.append(f"故障码: {ticket.fault_code}")

        lines.extend(["", "【候选工程师】"])
        for i, e in enumerate(engineers[:_MAX_ENGINEERS]):
            prod_parts = []
            for p, mods in e.responsibility_modules.items():
                prod_parts.append(f"[{p}]{','.join(mods)}" if mods else f"[{p}]")
            duty = (e.duty_text or "")[:120]
            dep = f"({e.department})" if e.department else ""
            lines.append(f"候选ID: {e.id} | L{e.job_level} | {dep}")
            lines.append(f"   产品:{'|'.join(prod_parts)}")
            if duty:
                lines.append(f"   职责:{duty}")

        lines.extend([
            "",
            "输出 JSON。engineer_id 必须填写候选列表中对应的 id 值（精确复制）。confidence 填 0~1 的浮点数。",
            '{"rankings":[{"engineer_id":"oD5oY3RN...","confidence":0.85},...]}',
        ])
        return "\n".join(lines)

    def _parse(self, response: str, engineers: List[EngineerProfile]) -> Dict[str, float]:
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            return {}
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return {}

        rankings = data.get("rankings", [])
        if not isinstance(rankings, list):
            return {}

        id_map = {e.id: e for e in engineers}
        scores = {}
        for r in rankings:
            eid = r.get("engineer_id", "").strip()
            conf = float(r.get("confidence", 0.0))
            if eid in id_map and conf > 0:
                scores[eid] = min(conf, 1.0)

        return scores
