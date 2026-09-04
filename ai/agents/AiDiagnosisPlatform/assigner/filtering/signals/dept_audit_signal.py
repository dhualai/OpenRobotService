"""R-Audit：部门派发审查（单轮 LLM 复核）。

在 R2(LLM 判部门) + R3(历史) 融合出「建议部门」之后，做一次独立的单轮复核，
专门确认"这个部门派得对不对"。审查看到**全部部门的语义画像**：
- 审核通过(ok=true)   → 维持原判部门；
- 审核判错(ok=false)   → 给出纠正部门(correct_dept) + 理由；
- 审核不确定/置信低    → 交由上层"打回重判"（最多 1 次），仍不定则落到兜底部门。

职责分工：R2=主判（第一意见），本审查=审核/纠错（post-validator），
不与 R2 平级抢判，只做质检，防止"单个 LLM 误判部门 → 派错部门"。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


@dataclass
class DeptAuditResult:
    ok: bool = False              # 建议部门是否通过审核
    correct_dept: str = ""        # ok=False 时给出的纠正部门
    confidence: float = 0.0       # 审查置信（0~1）
    reason: str = ""              # 理由
    audit_failed: bool = False    # LLM 调用/解析是否失败


class DeptAuditSignal:
    """部门派发审查信号：对建议部门做单轮 LLM 复核。"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._departments: List[dict] = self._config.departments or []
        self._audit_cfg = (self._config.department_routing or {}).get("audit") or {}

    @property
    def enabled(self) -> bool:
        # 总开关由上层 dept_router 通过 config 控制；这里判断是否有部门画像可审
        return bool(self._departments)

    def _build_prompt(self, ticket: TicketContext, suggested_dept: str) -> str:
        dept_blocks = []
        for dept in self._departments:
            name = dept.get("name") or ""
            if not name:
                continue
            profile = (dept.get("profile_text") or "").strip()
            dept_blocks.append(f"---\n部门：{name}\n{profile}")
        return (
            "你是工单部门派发审查员。系统已把工单初步判给某个部门，请你复核这个判断是否正确。\n"
            "请基于工单内容与各部门职责画像（**负责什么/不负责什么**）独立判断，"
            "不要被原判部门带偏。\n\n"
            "【全部部门画像】\n" + "\n".join(dept_blocks) + "\n\n"
            "【工单】\n"
            f"标题：{ticket.title or ''}\n"
            f"描述：{ticket.problem_description or ''}\n"
            f"故障码：{ticket.fault_code or '无'}\n"
            f"车型：{ticket.robot_type or '无'}\n"
            f"项目：{ticket.project_name or '无'}\n\n"
            f"【系统初步判定部门】\n{suggested_dept or '未确定'}\n\n"
            "请复核并输出 JSON（不要其它文字）：\n"
            '{"ok": true, "correct_dept": "<若ok=false给出正确部门，ok=true可为空>", '
            '"confidence": 0.0, "reason": "一句话理由"}'
        )

    @staticmethod
    def _parse(response: str) -> DeptAuditResult:
        m = re.search(r"\{.*\}", response or "", re.DOTALL)
        if not m:
            return DeptAuditResult(audit_failed=True)
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return DeptAuditResult(audit_failed=True)
        ok = bool(data.get("ok"))
        correct = (data.get("correct_dept") or "").strip()
        try:
            conf = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        return DeptAuditResult(
            ok=ok,
            correct_dept=correct,
            confidence=min(max(conf, 0.0), 1.0),
            reason=(data.get("reason") or "").strip(),
        )

    async def audit(self, ticket: TicketContext, suggested_dept: str) -> DeptAuditResult:
        """对建议部门做单轮审查。LLM 失败返回 audit_failed 结果（不阻断，由上层兜底）。"""
        if not suggested_dept or not self._departments:
            return DeptAuditResult(audit_failed=True, reason="无部门可审或未给建议部门")
        prompt = self._build_prompt(ticket, suggested_dept)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=200, temperature=0.0)
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] Step1 部门审查 LLM 调用失败: {e}")
            return DeptAuditResult(audit_failed=True, reason="审查LLM调用失败")

        result = self._parse(response)
        if result.audit_failed:
            logger.warning(f"[派单:{ticket.id}] Step1 部门审查 解析失败: {response[:200]}")
            return result

        # 校验纠正部门必须是合法部门（审查不可自造不存在的部门名）
        allowed = {d.get("name") for d in self._departments if d.get("name")}
        if result.correct_dept and result.correct_dept not in allowed:
            result.correct_dept = ""
            result.confidence = 0.0  # 视为"判错但纠正不明确"

        logger.info(
            f"[派单:{ticket.id}] Step1 部门审查 | 原判={suggested_dept} "
            f"审查ok={result.ok} 纠正={result.correct_dept or '-'} "
            f"置信={result.confidence:.2f} 理由='{result.reason[:60]}'"
        )
        return result
