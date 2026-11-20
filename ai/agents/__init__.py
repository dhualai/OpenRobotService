"""agents 包：顶层不预加载诊断平台（避免后端轻量 import 时拖入 numpy）。"""

from __future__ import annotations

__all__ = [
    "AiDiagnosisPlatform",
    "DiagnosisRequest",
    "AgentState",
    "get_diagnosis_platform",
]


def __getattr__(name: str):
    if name in __all__:
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            AiDiagnosisPlatform,
            DiagnosisRequest,
            AgentState,
            get_diagnosis_platform,
        )

        mapping = {
            "AiDiagnosisPlatform": AiDiagnosisPlatform,
            "DiagnosisRequest": DiagnosisRequest,
            "AgentState": AgentState,
            "get_diagnosis_platform": get_diagnosis_platform,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
