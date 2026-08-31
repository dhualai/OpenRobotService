"""候选池分层收紧：部门 → 产品 路由结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class DeptRoutingResult:
    """部门路由（LLM + 历史 融合 + R-Audit 复核）。"""
    primary_dept: str = ""
    confidence: float = 0.0
    margin: float = 0.0
    mode: str = "no_filter"  # hard_filter | soft_prior | no_filter
    dept_scores: Dict[str, float] = field(default_factory=dict)
    signals: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


@dataclass
class ProductRoutingResult:
    """Layer 2：产品收紧。"""

    product: str = ""
    mode: str = "no_filter"  # hard_filter | no_filter
    source: str = ""  # project_marker | default | skipped
    reasoning: str = ""


@dataclass
class TightenResult:
    """收紧汇总（部门 → 产品 两层）。"""

    candidates: list  # List[EngineerProfile]，运行时避免循环 import
    before_count: int = 0
    after_count: int = 0
    dept: DeptRoutingResult = field(default_factory=DeptRoutingResult)
    product: ProductRoutingResult = field(default_factory=ProductRoutingResult)
