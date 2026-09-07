"""Step7 兜底：只派对接人或项目经理，不再看精排。"""

from typing import Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import AssignmentResult

REASON_VAGUE = "描述含模糊强信号，无法可靠召回"
REASON_STEP6 = "当前画像下难以判定合适接单人"


class FallbackDecision:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    def decide(
        self,
        *,
        contact_id: Optional[str] = None,
        contact_name: Optional[str] = None,
        project_pm_id: Optional[str] = None,
        project_pm_name: Optional[str] = None,
        config_pm_id: Optional[str] = None,
        config_pm_name: Optional[str] = None,
        reason: str = REASON_STEP6,
    ) -> Optional[AssignmentResult]:
        """顺序：本单对接人 → 本单项目经理 → 配置项目经理。都空则 None（不拔精排 #1）。"""
        reason = (reason or "").strip() or REASON_STEP6
        if contact_id:
            return self._pack(contact_id, contact_name, reason)
        if project_pm_id:
            return self._pack(project_pm_id, project_pm_name, reason)
        if config_pm_id:
            return self._pack(config_pm_id, config_pm_name, reason)
        return None

    @staticmethod
    def _pack(eid: str, name: Optional[str], reason: str) -> AssignmentResult:
        return AssignmentResult(
            engineer_id=eid,
            engineer_name=(name or "").strip() or eid,
            confidence_score=0.0,
            reasoning=reason,
            decision_type="fallback",
        )
