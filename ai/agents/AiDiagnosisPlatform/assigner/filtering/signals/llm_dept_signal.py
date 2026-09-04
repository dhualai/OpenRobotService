"""R2：轻量 LLM 部门分类（基于 departments 画像）。"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext, dispatch_hint_text
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

    def _build_prompt(self, ticket: TicketContext, feedback: str = "") -> str:
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

        # 提单信息充分性信号（有值才注入一行；信息充分 dispatch_hint 为空）
        _dh = dispatch_hint_text(getattr(ticket, "dispatch_hint", None))
        _dh_line = f"{_dh}\n" if _dh else ""

        # 工单类型 → 判定逻辑分支（对齐提单 Agent 的 5 类：problem/bug/feature/support/other）
        ticket_type = (ticket.ticket_type or "other").strip().lower()
        type_text = {
            "problem": "问题/报障(problem)：描述异常现象，按【故障现象】归到负责该故障的部门",
            "bug": "缺陷(bug)：描述软件缺陷，按【故障现象】归到负责该故障的部门",
            "feature": "需求(feature)：希望加功能/提需求，无故障现象，按【工单涉及的产品/项目】归到管理该产品的部门",
            "support": "咨询(support)：使用方法/操作指导/配置协助，按【咨询涉及的产品/项目】归到管理该产品的部门",
            "other": "其它(other)：结合内容判断，按涉及的产品/项目或现象归到对应部门",
        }[ticket_type] if ticket_type in "problem bug feature support other".split() else (
            "其它：结合内容判断，按涉及的产品/项目或故障现象归到对应部门"
        )

        type_extra = ""
        if ticket.scenario or ticket.expected_effect:
            type_extra += f"需求场景：{ticket.scenario or '无'}\n预期效果：{ticket.expected_effect or '无'}\n"
        if ticket.support_type:
            type_extra += f"支持类型：{ticket.support_type or '无'}\n"
        if ticket.severity or ticket.version:
            type_extra += f"严重程度：{ticket.severity or '无'}\n版本：{ticket.version or '无'}\n"

        return (
            "你是工单部门路由专家。请判断工单最可能由哪个部门负责处理。\n"
            "判断前请先看工单类型，按对应逻辑判部门（不要把所有工单都当故障）：\n"
            f"  - {type_text}\n"
            "请结合每个部门的【负责】与【不负责】边界：先用【不负责】排除明显无关的部门\n"
            "（避免仅凭表面字眼命中而误判），再看【负责】/【典型现象】确定归属。\n"
            "针对【部门清单】中的每一个部门，评估其负责该工单的可能性，并给出 confidence（0~1）：\n"
            "  - 0.85~1.0：明确负责（强归属，几乎确定）\n"
            "  - 0.55~0.85：很可能负责（主要候选，有较强证据）\n"
            "  - 0.25~0.55：有一定关联（次要候选，可能是交叉/多部门，请保留并给合理分数）\n"
            "  - 0~0.25：基本不相关（被【不负责】排除或明显无关）\n"
            "对跨部门/交叉工单，请明确区分主导部门与次要涉及部门：主导给高分(0.85+)，\n"
            "次要给中等分(0.3~0.5)，避免所有候选都挤在 0.6~0.7 无法区分。\n"
            "请按 confidence 降序输出最多 3 个最有把握的部门（若存在合理的次要候选也一并输出，\n"
            "不要只给 0.9 一个高分而把其余全部压到 0.1）；若确实无法判断则输出空数组，不要强行给分。\n\n"
            "【部门清单】\n"
            + "\n".join(dept_blocks)
            + "\n\n【工单】\n"
            f"工单类型：{ticket_type}\n"
            f"标题：{ticket.title or ''}\n"
            f"描述：{ticket.problem_description or ''}\n"
            + type_extra
            + f"故障码：{ticket.fault_code or '无'}\n"
            f"车型：{ticket.robot_type or '无'}\n"
            f"项目：{ticket.project_name or '无'}\n"
            f"Agent假设：{hypotheses or '无'}\n"
            + _dh_line
            + (
                "\n【审查反馈（上一轮部门审查的意见，供你重新判定时参考，请审慎采纳）】"
                f"\n{feedback}\n"
                if feedback
                else ""
            )
            + "\n输出 JSON（不要其它文字）：\n"
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

    async def classify(self, ticket: TicketContext, feedback: str = "") -> Dict[str, float]:
        """返回 {部门名: confidence}。feedback 可选：附加"上一轮审查意见"供重判参考。"""
        if not self.enabled:
            return {}

        allowed = {d.get("name") for d in self._departments if d.get("name")}
        if not allowed:
            return {}

        min_conf = float(self._llm_cfg.get("min_confidence", 0.3))
        prompt = self._build_prompt(ticket, feedback=feedback)
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
