"""Assigner（智能派单） — AiDiagnosisPlatform 子模块

工单生成后自动推荐负责人。

便捷入口：
    from ai.agents.AiDiagnosisPlatform.assigner import assign_ticket

    result = await assign_ticket(
        title="AGV小车无法启动",
        problem_description="潜伏车上线后无法移动",
    )
    print(result.engineer_name, result.confidence_score)
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.assigner import Assigner
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
    "assign_ticket",
]

# ── 模块级缓存 ──────────────────────────────────────────────

_engineers_cache: Optional[List[EngineerProfile]] = None
_DATA_DIR = Path(__file__).parent / "data"


def load_engineers(reload: bool = False) -> List[EngineerProfile]:
    """加载工程师画像（模块级缓存）。

    Priority: engineers.json → engineers.example.json → []

    Args:
        reload: True 时强制重新读取文件，否则复用缓存。
    """
    global _engineers_cache
    if _engineers_cache is not None and not reload:
        return _engineers_cache

    path = _DATA_DIR / "engineers.json"
    example_path = _DATA_DIR / "engineers.example.json"
    chosen = path if path.exists() else (example_path if example_path.exists() else None)

    if chosen is None:
        _engineers_cache = []
        return []

    with open(chosen, "r", encoding="utf-8") as f:
        raw = json.load(f)
    _engineers_cache = [EngineerProfile(**item) for item in raw]
    return _engineers_cache


# ── 一站式派单入口 ──────────────────────────────────────────

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
    """一站式派单：加载工程师 → 构建工单上下文 → 四层流水线派单。

    调用方只需传工单信息，无需关心工程师数据加载和 Assigner 实例化。
    失败时直接抛出异常，调用方自行 catch。

    Args:
        title: 工单标题
        problem_description: 问题描述
        ticket_id: 工单唯一标识（未提供时自动生成）
        其余见 TicketContext 字段。

    Returns:
        AssignmentResult: 推荐的工程师及置信度。

    Raises:
        ValueError: 工程师画像未配置。
    """
    engineers = load_engineers()
    if not engineers:
        raise ValueError("工程师画像未配置，请检查 data/engineers.json")

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
