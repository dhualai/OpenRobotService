"""AiDiagnosisPlatform 包：顶层不预加载 pipeline / assigner 重依赖。

后端归档审核配置与派审桥接会 import ``assigner.settings`` / ``archive_review``，
若此处顶层拉诊断流水线，会间接要求 numpy（仅 AI 环境有）。
"""

from __future__ import annotations

__all__ = [
    "AiDiagnosisPlatform",
    "DiagnosisRequest",
    "AgentState",
    "get_diagnosis_platform",
    "DispatchFlow",
    "AssignmentResult",
    "EngineerProfile",
    "TicketContext",
]


def __getattr__(name: str):
    if name in (
        "AiDiagnosisPlatform",
        "DiagnosisRequest",
        "AgentState",
        "get_diagnosis_platform",
    ):
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            AiDiagnosisPlatform,
            DiagnosisRequest,
            AgentState,
            get_diagnosis_platform,
        )

        return {
            "AiDiagnosisPlatform": AiDiagnosisPlatform,
            "DiagnosisRequest": DiagnosisRequest,
            "AgentState": AgentState,
            "get_diagnosis_platform": get_diagnosis_platform,
        }[name]
    if name in (
        "DispatchFlow",
        "AssignmentResult",
        "EngineerProfile",
        "TicketContext",
    ):
        from ai.agents.AiDiagnosisPlatform import assigner as _assigner

        return getattr(_assigner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
