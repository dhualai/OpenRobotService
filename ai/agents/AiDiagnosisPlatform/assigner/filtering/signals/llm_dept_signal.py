"""R2：轻量 LLM 部门分类（基于 departments 画像）。"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class LlmDeptSignal:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._routing_cfg = self._config.department_routing or {}
        self._llm_cfg = self._routing_cfg.get("llm") or {}
        self._departments: List[dict] = self._config.departments or []

    @property
    def enabled(self) -> bool:
        return bool(self._llm_cfg.get("enabled", True)) and bool(self._departments)

    def _build_prompt(self, ticket: TicketContext) -> str:
        dept_blocks = []
        for dept in self._departments:
            name = dept.get("name") or ""
            if not name:
                continue
            profile = (dept.get("profile_text") or "").strip()
            examples = dept.get("examples") or []
            ex_lines = []
            for ex in examples[:3]:
                if isinstance(ex, dict):
                    ex_lines.append(f"  示例：{ex.get('title', '')} → {ex.get('dept', name)}")
            dept_blocks.append(
                f"---\n部门：{name}\n{profile}\n" + ("\n".join(ex_lines) if ex_lines else "")
            )

        hypotheses = ""
        if ticket.diagnosis_hypotheses:
            hypotheses = "；".join(ticket.diagnosis_hypotheses[:5])

        return (
            "你是工单部门路由专家。根据工单内容，判断最可能负责处理的部门。\n"
            "只能从下列部门中选择，按 confidence 降序输出最多 3 个。\n\n"
            "【部门清单】\n"
            + "\n".join(dept_blocks)
            + "\n\n【工单】\n"
            f"标题：{ticket.title or ''}\n"
            f"描述：{ticket.problem_description or ''}\n"
            f"故障码：{ticket.fault_code or '无'}\n"
            f"车型：{ticket.robot_type or '无'}\n"
            f"项目：{ticket.project_name or '无'}\n"
            f"Agent假设：{hypotheses or '无'}\n\n"
            "输出 JSON（不要其它文字）：\n"
            '{"departments":[{"name":"部门名","confidence":0.0,"reason":"一句话"}]}'
        )

    @staticmethod
    def _parse(response: str, allowed: set) -> List[dict]:
        m = re.search(r"\{.*\}", response or "", re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return []
        items = data.get("departments") or []
        out = []
        for item in items:
            name = (item.get("name") or "").strip()
            if name not in allowed:
                continue
            try:
                conf = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                conf = 0.0
            conf = max(0.0, min(1.0, conf))
            out.append({
                "name": name,
                "confidence": conf,
                "reason": (item.get("reason") or "").strip(),
            })
        out.sort(key=lambda x: x["confidence"], reverse=True)
        return out[:3]

    async def classify(self, ticket: TicketContext) -> Dict[str, float]:
        """返回 {部门名: confidence}。"""
        if not self.enabled:
            return {}

        allowed = {d.get("name") for d in self._departments if d.get("name")}
        if not allowed:
            return {}

        min_conf = float(self._llm_cfg.get("min_confidence", 0.75))
        prompt = self._build_prompt(ticket)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(
                prompt,
                max_tokens=int(self._llm_cfg.get("max_tokens", 250)),
                temperature=float(self._llm_cfg.get("temperature", 0)),
            )
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] R2-LLM部门分类失败: {e}")
            return {}

        items = self._parse(response, allowed)
        scores = {
            item["name"]: item["confidence"]
            for item in items
            if item["confidence"] >= min_conf
        }
        if items:
            logger.info(
                f"[派单:{ticket.id}] R2-LLM部门 "
                + " | ".join(
                    f"{it['name']}={it['confidence']:.2f}({it['reason'][:30]})"
                    for it in items[:3]
                )
            )
        return scores
