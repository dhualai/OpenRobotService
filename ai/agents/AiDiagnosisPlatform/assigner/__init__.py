"""Assigner（智能派单） — AiDiagnosisPlatform 子模块

工单生成后自动推荐负责人。
"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow
from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import (
    load_engineers,
    invalidate_cache as invalidate_personnel_cache,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult,
    EngineerProfile,
    TicketContext,
)
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

__all__ = [
    "DispatchFlow",
    "AssignmentResult",
    "EngineerProfile",
    "TicketContext",
    "load_engineers",
    "invalidate_personnel_cache",
    "assign_ticket",
    "ensure_dispatch_ready",
]

# 进程级 DispatchFlow 单例（避免每张工单重复加载配置/构建流水线）
_dispatch_singleton: Optional[DispatchFlow] = None


def ensure_dispatch_ready() -> DispatchFlow:
    """预热并返回进程级 DispatchFlow 单例（Worker 启动时调用，避免首单卡顿）。

    首次调用会加载配置 + 构建整个派单流水线（部门过滤/召回/精排/决策），
    之后复用单例。支持配置热更新后调用 reload_config() 刷新。
    """
    global _dispatch_singleton
    if _dispatch_singleton is None:
        _dispatch_singleton = DispatchFlow()
        logger.info("派单 DispatchFlow 预热完成（配置 + 流水线就绪）")
    return _dispatch_singleton


# 外部调用函数
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
    project_name: Optional[str] = None,  # 预留：未来按项目缩小范围
    project_id: Optional[str] = None,
    required_skills: Optional[List[str]] = None,
    diagnosis_hypotheses: Optional[List[str]] = None,
    diagnosis_ruled_out: Optional[List[str]] = None,
    diagnosis_collected_info: Optional[Dict[str, str]] = None,
    diagnosis_rounds: Optional[int] = None,
    contact: Optional[str] = None,
    creator: Optional[str] = None,
) -> AssignmentResult:
    global _dispatch_singleton
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
        project_name=project_name,
        project_id=project_id or "", 
        required_skills=required_skills or [],
        diagnosis_hypotheses=diagnosis_hypotheses,
        diagnosis_ruled_out=diagnosis_ruled_out,
        diagnosis_collected_info=diagnosis_collected_info,
        diagnosis_rounds=diagnosis_rounds,
        contact=contact,
        creator=creator,
    )

    # 复用进程级 DispatchFlow 单例（由 ensure_dispatch_ready 懒加载/预热）
    dispatch = ensure_dispatch_ready()
    return await dispatch.aassign(ctx, engineers)
