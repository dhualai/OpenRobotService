"""Assigner（智能派单） — AiDiagnosisPlatform 子模块

工单生成后自动推荐负责人。
"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.assigner import Assigner
from ai.agents.AiDiagnosisPlatform.assigner.personnel_sync import (
    load_engineers,
    invalidate_cache as invalidate_personnel_cache,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult,
    EngineerProfile,
    TicketContext,
)

__all__ = [
    "Assigner",
    "AssignmentResult",
    "EngineerProfile",
    "TicketContext",
    "load_engineers",
    "invalidate_personnel_cache",
    "assign_ticket",
]


async def assign_ticket(
    *,
    title: str,
    problem_description: str,
    ticket_id: str = "",
    status: str = "待派单",
    priority: str = "中",
    ticket_type: str = "问题",
    session_id: Optional[str] = None,
    source: Optional[str] = None,
    location: Optional[str] = None,
    robot_type: Optional[str] = None,
    fault_code: Optional[str] = None,
    special_notes: Optional[str] = None,
    required_skills: Optional[List[str]] = None,
    diagnosis_hypotheses: Optional[List[str]] = None,
    diagnosis_ruled_out: Optional[List[str]] = None,
    diagnosis_collected_info: Optional[Dict[str, str]] = None,
    diagnosis_rounds: Optional[int] = None,
    contact: Optional[str] = None,
    creator: Optional[str] = None,
) -> AssignmentResult:
    engineers = load_engineers()
    if not engineers:
        raise ValueError("工程师画像为空，请检查 users 表人员数据是否就绪")

    if not ticket_id:
        import time
        ticket_id = f"ticket_{int(time.time())}"

    ctx = TicketContext(
        id=ticket_id,
        title=title,
        problem_description=problem_description,
        status=status,
        priority=priority,
        ticket_type=ticket_type,
        session_id=session_id,
        source=source,
        location=location,
        robot_type=robot_type,
        fault_code=fault_code,
        special_notes=special_notes,
        required_skills=required_skills or [],
        diagnosis_hypotheses=diagnosis_hypotheses,
        diagnosis_ruled_out=diagnosis_ruled_out,
        diagnosis_collected_info=diagnosis_collected_info,
        diagnosis_rounds=diagnosis_rounds,
        contact=contact,
        creator=creator,
    )

    assigner = Assigner()
    return await assigner.aassign(ctx, engineers)
